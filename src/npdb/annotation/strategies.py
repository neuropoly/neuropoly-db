"""
Strategy Pattern for annotation execution modes.

Each concrete strategy encapsulates the logic for one annotation mode
(manual / assist / auto / full-auto).  AnnotationStrategyFactory maps
an AnnotationConfig to the right strategy so that NeurobagelAnnotator.execute()
never needs an if/elif mode branch.
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from npdb.annotation import AnnotationConfig
from npdb.annotation.utils import parse_tsv_columns
from npdb.automation.mappings.resolvers import MappingResolver
from npdb.external.neurobagel.automation import NBAnnotationToolBrowserSession
from npdb.report.provenance import ProvenanceReport


@dataclass
class AnnotatorContext:
    """Shared state threaded through every annotation strategy."""

    config: AnnotationConfig
    resolver: MappingResolver
    provenance: ProvenanceReport
    # async (tsv_path, output_dir, annotations_dict) -> None
    save_outputs: Callable[..., Awaitable[None]]


# ---------------------------------------------------------------------------
# Module-level helpers shared by multiple strategies
# ---------------------------------------------------------------------------


def _to_annotations_dict(resolved: list) -> dict:
    """Convert a list of ResolvedMappings to the annotations output dict."""
    return {
        mapping.column_name: {
            "variable": mapping.mapped_variable,
            "source": mapping.source,
            "confidence": mapping.confidence,
            "rationale": mapping.rationale,
        }
        for mapping in resolved
        if mapping.source != "unresolved"
    }


def _partition_resolved(
    resolved: list, threshold: float | None
) -> tuple[list, list, list]:
    """Partition resolved mappings into (accepted, rejected, unresolved) in one pass.

    Static-source mappings always go into *accepted* regardless of threshold.
    """
    accepted, rejected, unresolved = [], [], []
    for m in resolved:
        if m.source == "unresolved":
            unresolved.append(m)
        elif threshold is None or m.confidence >= threshold or m.source == "static":
            accepted.append(m)
        else:
            rejected.append(m)
    return accepted, rejected, unresolved


class AnnotationStrategy(ABC):
    """Abstract base class for annotation execution strategies."""

    @abstractmethod
    async def run(
        self,
        participants_tsv_path: Path,
        output_dir: Path,
        ctx: AnnotatorContext,
    ) -> bool:
        """Execute the annotation strategy and return True on success."""
        ...


class ManualStrategy(AnnotationStrategy):
    """
    Manual mode: open browser headed and wait for the user to annotate.
    """

    async def run(
        self,
        participants_tsv_path: Path,
        output_dir: Path,
        ctx: AnnotatorContext,
    ) -> bool:
        try:
            async with NBAnnotationToolBrowserSession(
                headless=False,  # Manual always headed
                timeout=ctx.config.timeout,
                artifacts_dir=ctx.config.artifacts_dir,
            ) as session:
                await session.navigate_to()
                print(
                    f"✓ Opened annotation tool: {NBAnnotationToolBrowserSession.ANNOTATION_URL}"
                )

                await session.click_get_started()
                print(f"✓ Clicked 'Get Started' button")
                print(f"✓ Upload participants.tsv: {participants_tsv_path}")

                # Wait for completion signal (long timeout, user-driven)
                # TODO: Replace sleep with file-system monitoring or browser
                # event detection to detect when user exports data.
                await asyncio.sleep(ctx.config.timeout)

                # Assume outputs are downloaded to default location
                return True

        except Exception as e:
            print(f"✗ Manual mode error: {e}")
            return False


class AssistStrategy(AnnotationStrategy):
    """
    Assist mode: automated prefill + AI suggestions + user finalisation.

    Attempts best-effort save via offline column resolution if the browser
    upload fails.
    """

    async def run(
        self,
        participants_tsv_path: Path,
        output_dir: Path,
        ctx: AnnotatorContext,
    ) -> bool:
        browser_upload_failed = False

        try:
            async with NBAnnotationToolBrowserSession(
                headless=ctx.config.headless,
                timeout=ctx.config.timeout,
                artifacts_dir=ctx.config.artifacts_dir,
            ) as session:
                # Step 0: Navigate to landing page
                await session.navigate_to()
                print(f"✓ Opened annotation tool")

                # Step 1: Click "Get Started" and upload
                try:
                    await session.click_get_started()
                    print(f"✓ Clicked 'Get Started' button")

                    await session.upload_file(participants_tsv_path, file_type="tsv")
                    print(f"✓ Uploaded {participants_tsv_path.name}")

                    if (
                        ctx.config.phenotype_dictionary
                        and ctx.config.phenotype_dictionary.exists()
                    ):
                        await session.upload_file(
                            ctx.config.phenotype_dictionary, file_type="json"
                        )
                        print(
                            f"✓ Uploaded phenotype dictionary: "
                            f"{ctx.config.phenotype_dictionary.name}"
                        )

                except Exception as e:
                    print(f"⚠ Warning: Browser upload failed: {e}")
                    print(
                        f"⚠ Will attempt offline resolution and save best-effort output"
                    )
                    browser_upload_failed = True

                # Step 2: Resolve columns (works offline)
                try:
                    column_names = parse_tsv_columns(participants_tsv_path)
                except Exception as e:
                    print(
                        f"⚠ Warning: Could not parse TSV columns: {e}. "
                        f"Using placeholder columns."
                    )
                    column_names = ["participant_id", "age", "sex"]

                resolved = ctx.resolver.resolve_columns(column_names)
                print(
                    f"✓ Resolved {len(resolved)} columns "
                    f"(assist mode: showing suggestions)"
                )

                for mapping in resolved:
                    if mapping.source == "unresolved":
                        continue
                    ctx.provenance.add_column_provenance(
                        column_name=mapping.column_name,
                        source=mapping.source,
                        confidence=mapping.confidence,
                        variable=mapping.mapped_variable,
                        rationale=mapping.rationale,
                    )

                if not browser_upload_failed:
                    print(f"✓ Assist mode: User reviews and finalizes in browser")
                    # TODO: Replace sleep with download event detection or
                    # file polling instead of a static timeout.
                    await asyncio.sleep(ctx.config.timeout)

                annotations_dict = _to_annotations_dict(resolved)

                await ctx.save_outputs(
                    participants_tsv_path, output_dir, annotations_dict
                )

                provenance_path = output_dir / "phenotypes_provenance.json"
                ctx.provenance.save(provenance_path)
                print(f"✓ Saved provenance: {provenance_path}")

                if browser_upload_failed:
                    print(
                        f"⚠ Assist mode completed with offline resolution "
                        f"(browser upload failed)"
                    )
                    ctx.provenance.warnings.append(
                        "Browser upload failed; outputs from offline resolution"
                    )

                return True

        except Exception as e:
            print(f"✗ Assist mode error: {e}")
            print(f"✗ Exception type: {type(e).__name__}, Details: {str(e)}")

            # Attempt emergency save of best-effort output
            try:
                print(f"✓ Attempting emergency save of best-effort output...")
                column_names = (
                    parse_tsv_columns(participants_tsv_path)
                    if participants_tsv_path.exists()
                    else []
                )
                resolved = (
                    ctx.resolver.resolve_columns(column_names) if column_names else []
                )

                annotations_dict = _to_annotations_dict(resolved)

                if annotations_dict:
                    await ctx.save_outputs(
                        participants_tsv_path, output_dir, annotations_dict
                    )
                    ctx.provenance.warnings.append(
                        f"Partial failure: {str(e)}; saved offline resolution"
                    )
                    provenance_path = output_dir / "phenotypes_provenance.json"
                    ctx.provenance.save(provenance_path)
                    print(f"✓ Emergency save completed")
                    return False

            except Exception as emergency_e:
                print(f"✗ Emergency save also failed: {emergency_e}")

            return False


class _ScriptedStrategy(AnnotationStrategy):
    """
    Shared implementation for scripted (auto / full-auto) annotation modes.

    Subclasses declare their confidence threshold and mode-specific messages
    via class attributes; the full execution pipeline is implemented once here.
    """

    # Minimum confidence required to include a resolved mapping in output.
    # ``None`` means "no threshold; include all resolved mappings".
    _CONFIDENCE_THRESHOLD: float | None = 0.7
    # Human-readable label used in log messages.
    _MODE_LABEL: str = "Auto"
    # Optional preamble lines printed before the browser launches.
    _PREAMBLE: list[str] = []

    async def run(
        self,
        participants_tsv_path: Path,
        output_dir: Path,
        ctx: AnnotatorContext,
    ) -> bool:
        for line in self._PREAMBLE:
            print(line)

        current_step = "initialization"
        try:
            current_step = "browser_launch"
            async with NBAnnotationToolBrowserSession(
                headless=ctx.config.headless,
                timeout=ctx.config.timeout,
                artifacts_dir=ctx.config.artifacts_dir,
            ) as session:
                current_step = "navigation"
                await session.navigate_to()
                print(f"✓ {self._MODE_LABEL} mode: Opened annotation tool")

                current_step = "get_started_click"
                await session.click_get_started()
                print(f"✓ {self._MODE_LABEL} mode: Clicked 'Get Started' button")

                current_step = "file_upload"
                await session.upload_file(participants_tsv_path, file_type="tsv")
                print(f"✓ Uploaded {participants_tsv_path.name}")

                if (
                    ctx.config.phenotype_dictionary
                    and ctx.config.phenotype_dictionary.exists()
                ):
                    await session.upload_file(
                        ctx.config.phenotype_dictionary, file_type="json"
                    )
                    print(
                        f"✓ Uploaded phenotype dictionary: "
                        f"{ctx.config.phenotype_dictionary.name}"
                    )

                current_step = "column_resolution"
                try:
                    column_names = parse_tsv_columns(participants_tsv_path)
                except Exception as e:
                    print(
                        f"⚠ Warning: Could not parse TSV columns: {e}. "
                        f"Using placeholder columns."
                    )
                    column_names = ["participant_id", "age", "sex"]

                resolved = ctx.resolver.resolve_columns(column_names)
                threshold = self._CONFIDENCE_THRESHOLD

                accepted, rejected, unresolved = _partition_resolved(
                    resolved, threshold
                )

                print(
                    f"✓ {self._MODE_LABEL} mode: {len(accepted)} accepted, "
                    f"{len(rejected)} below-threshold, "
                    f"{len(unresolved)} unresolved"
                )

                for mapping in accepted:
                    ctx.provenance.add_column_provenance(
                        column_name=mapping.column_name,
                        source=mapping.source,
                        confidence=mapping.confidence,
                        variable=mapping.mapped_variable,
                        rationale=mapping.rationale,
                    )

                if rejected:
                    print(
                        f"⚠ Warning: {len(rejected)} columns below confidence threshold"
                    )
                    for m in rejected:
                        ctx.provenance.warnings.append(
                            f"Low confidence mapping for '{m.column_name}': "
                            f"{m.rationale}"
                        )

                self._add_mode_warnings(ctx)

                current_step = "output_generation"
                annotations_dict = _to_annotations_dict(accepted)

                await ctx.save_outputs(
                    participants_tsv_path, output_dir, annotations_dict
                )

                provenance_path = output_dir / "phenotypes_provenance.json"
                ctx.provenance.save(provenance_path)
                print(f"✓ {self._MODE_LABEL} mode completed successfully")
                print(f"✓ Provenance saved: {provenance_path}")

                return True

        except Exception as e:
            print(
                f"✗ {self._MODE_LABEL} mode error at step '{current_step}': "
                f"{type(e).__name__}"
            )
            print(f"✗ Error details: {str(e)}")
            if ctx.config.artifacts_dir:
                print(f"✗ Check artifacts in: {ctx.config.artifacts_dir}")
            ctx.provenance.warnings.append(
                f"{self._MODE_LABEL} mode failed at step '{current_step}': {str(e)}"
            )
            return False

    def _add_mode_warnings(self, ctx: AnnotatorContext) -> None:
        """Hook for subclasses to inject mode-specific provenance warnings."""


class AutoStrategy(_ScriptedStrategy):
    """
    Auto mode: full scripted automation with bounded AI (confidence >= 0.7).
    """

    _CONFIDENCE_THRESHOLD = 0.7
    _MODE_LABEL = "Auto"


class FullAutoStrategy(_ScriptedStrategy):
    """
    Full-auto mode: fully autonomous with lenient confidence thresholds (>= 0.5).

    EXPERIMENTAL / UNSTABLE — review provenance sidecar before use.
    """

    _CONFIDENCE_THRESHOLD = 0.5
    _MODE_LABEL = "Full-auto"
    _PREAMBLE = [
        "⚠️  EXPERIMENTAL/UNSTABLE: Full-auto mode activated",
        "⚠️  Using lenient confidence thresholds (>= 0.5)",
        "⚠️  Review provenance sidecar carefully before use",
    ]

    def _add_mode_warnings(self, ctx: AnnotatorContext) -> None:
        ctx.provenance.warnings.append(
            "⚠️ FULL-AUTO MODE: All mappings are AI or fuzzy-assisted; "
            "review required"
        )


class AnnotationStrategyFactory:
    """Maps an AnnotationConfig.mode to the corresponding strategy class."""

    _REGISTRY: dict[str, type[AnnotationStrategy]] = {
        "manual": ManualStrategy,
        "assist": AssistStrategy,
        "auto": AutoStrategy,
        "full-auto": FullAutoStrategy,
    }

    @classmethod
    def create(cls, config: AnnotationConfig) -> AnnotationStrategy:
        """Return a fresh strategy instance for *config.mode*."""
        strategy_cls = cls._REGISTRY.get(config.mode)
        if strategy_cls is None:
            raise ValueError(
                f"Unknown annotation mode '{config.mode}'. "
                f"Valid modes: {list(cls._REGISTRY)}"
            )
        return strategy_cls()
