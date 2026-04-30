
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple
from tenacity import retry, stop_after_attempt, wait_exponential

from npdb.managers.neurogitea import OrganizationMixin
from npdb.managers.neurobagel import BagelMixin, NeurobagelManager
from npdb.managers.bids import BIDSStandardizer
from npdb.external.neurogitea.gitea import GiteaManager
from npdb.external.neurobagel.errors import BagelCLIError


# ---------------------------------------------------------------------------
# BIDS suffixes supported by Bagel CLI for imaging data
# ---------------------------------------------------------------------------

BAGEL_SUPPORTED_SUFFIXES: frozenset = frozenset({
    "T1w", "T2w", "FLAIR", "bold", "dwi", "T2star", "T2starw",
    "T1map", "T2map", "PDw", "PDT2", "PDmap", "inplaneT1", "inplaneT2",
    "MTRmap", "MTsat", "T1rho", "fmap", "sbref", "epi",
    "magnitude1", "magnitude2", "phasediff", "phase1", "phase2",
})


# ---------------------------------------------------------------------------
# Pre-flight exception
# ---------------------------------------------------------------------------

class PreflightError(RuntimeError):
    """Raised when a pre-flight check fails before any Bagel CLI call."""

    def __init__(
        self,
        problem_name: str,
        description: str,
        fix_steps: Optional[List[dict]] = None,
        raw_snippet: str = "",
    ) -> None:
        super().__init__(description)
        self.problem_name = problem_name
        self.description = description
        self.fix_steps: List[dict] = fix_steps or []
        self.raw_snippet = raw_snippet


# ---------------------------------------------------------------------------
# Pre-flight check functions
# ---------------------------------------------------------------------------

def check_bids_suffixes(
    bids_dir: str,
    extra_suffix_map: Optional[Dict[str, str]] = None,
) -> Tuple[Set[str], Set[str]]:
    """
    Scan BIDS directory for NIfTI imaging files and classify their suffixes.

    *extra_suffix_map* is an optional ``{suffix: iri}`` dict that extends the
    built-in :data:`BAGEL_SUPPORTED_SUFFIXES`.  Suffixes present in this map
    are treated as supported and will not trigger a :class:`PreflightError`.

    Returns ``(supported_found, unsupported_found)``.  Raises
    :class:`PreflightError` if imaging files are present but none use a
    Bagel-supported BIDS suffix (including extra entries).
    """
    effective_supported = BAGEL_SUPPORTED_SUFFIXES | frozenset(
        extra_suffix_map or {}
    )
    bids_path = Path(bids_dir)
    supported: Set[str] = set()
    unsupported: Set[str] = set()

    for nii in bids_path.glob("sub-*/**/*.nii*"):
        stem = nii.name
        # Strip .nii.gz or .nii
        if stem.endswith(".nii.gz"):
            stem = stem[:-7]
        elif stem.endswith(".nii"):
            stem = stem[:-4]
        # BIDS suffix is the entity after the last underscore
        suffix = stem.rsplit("_", 1)[-1] if "_" in stem else stem
        if suffix in effective_supported:
            supported.add(suffix)
        else:
            unsupported.add(suffix)

    if not supported and unsupported:
        raise PreflightError(
            problem_name="preflight_failure",
            description=(
                "No supported BIDS imaging files found. "
                f"Dataset only contains unsupported suffixes: {sorted(unsupported)}"
            ),
            fix_steps=[
                {
                    "action": "Annotate phenotypes only (skip imaging steps)",
                    "detail": "Re-run gitea2bagel without the imaging pipeline. "
                    "Only bagel pheno will be called for this dataset.",
                    "auto_fixable": False,
                },
                {
                    "action": "Register the modality with --extend-modalities",
                    "detail": (
                        f"Unsupported suffixes: {sorted(unsupported)}. "
                        "Pass --extend-modalities to gitea2bagel so npdb can map each "
                        "suffix to a Neurobagel Image IRI (via LLM or built-in heuristic) "
                        "and retry the conversion automatically."
                    ),
                    "auto_fixable": True,
                },
                {
                    "action": "Open an upstream issue",
                    "detail": "Open an issue on https://github.com/neurobagel/bagel-cli/issues "
                    "to request native support for this imaging modality.",
                    "auto_fixable": False,
                },
            ],
        )

    return supported, unsupported


def compare_participant_ids(
    pheno_tsv: str, bids_tsv: str
) -> Tuple[List[str], List[str]]:
    """
    Compare participant IDs in phenotypes TSV vs BIDS TSV (case-insensitive).

    Returns ``(in_bids_not_pheno, in_pheno_not_bids)``.
    """
    import csv as _csv

    def _read_ids(path: str) -> Set[str]:
        ids: Set[str] = set()
        try:
            with open(path, "r", encoding="utf-8", newline="") as fh:
                reader = _csv.DictReader(fh, delimiter="\t")
                for row in reader:
                    pid = (row.get("participant_id") or "").strip().lower()
                    if pid:
                        ids.add(pid)
        except OSError:
            pass
        return ids

    pheno_ids = _read_ids(pheno_tsv)
    bids_ids = _read_ids(bids_tsv)

    in_bids_not_pheno = sorted(bids_ids - pheno_ids)
    in_pheno_not_bids = sorted(pheno_ids - bids_ids)
    return in_bids_not_pheno, in_pheno_not_bids


def check_missing_files(*paths: str) -> List[str]:
    """Return a list of paths from *paths* that do not exist on disk."""
    return [p for p in paths if not Path(p).exists()]


class DataNeuroPolyMTL(OrganizationMixin, GiteaManager):
    def __init__(self, url: str, user: str, token: str, ssl_verify: bool = True):
        GiteaManager.__init__(
            self, url=url, user=user, token=token, ssl_verify=ssl_verify)
        OrganizationMixin.__init__(
            self, organization="datasets", client=self.client)

    def clone_repository(
        self,
        dataset: str,
        local_path: str,
        light: bool = False,
        cache_dir: Optional[str] = None,
        output_callback: Optional[Callable[[str], None]] = None,
    ):
        repo = next(iter([d for d in self.datasets if d.name == dataset]))
        clone_url = f"{repo.gitea.url}/{self.organization.name}/{repo.name}.git"

        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"

        # Cache-dir mode: reuse an existing clone via fetch, or do a fresh clone.
        if cache_dir:
            cached = os.path.join(cache_dir, dataset)
            if os.path.isdir(os.path.join(cached, ".git")):
                command = ["git", "-C", cached, "fetch", "--depth=1"]
                self._run_git(command, env, output_callback)
                # Symlink / copy into local_path so the rest of the pipeline
                # continues to point at the expected directory.
                if not os.path.exists(local_path):
                    import shutil
                    shutil.copytree(cached, local_path, symlinks=True)
                return
            target = cached
        else:
            target = local_path

        command = ["git"] + self.git_http_config() + ["clone"]
        if light:
            command.extend(["--depth", "1", "--filter=blob:none"])
        command.extend([clone_url, target])

        self._run_git(command, env, output_callback)

        # When using cache_dir and the clone target differs from local_path,
        # copy into local_path so callers see the expected path.
        if cache_dir and target != local_path and not os.path.exists(local_path):
            import shutil
            shutil.copytree(target, local_path, symlinks=True)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
    def _run_git(
        self,
        command: list,
        env: dict,
        output_callback: Optional[Callable[[str], None]] = None,
        context: str = "",
    ):
        try:
            if output_callback is None:
                subprocess.run(command, check=True,
                               env=env, capture_output=True)
                return
            # Stream output to callback via Popen
            proc = subprocess.Popen(
                command,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            for line in proc.stdout:
                output_callback(line.rstrip())
            proc.wait()
            if proc.returncode != 0:
                raise subprocess.CalledProcessError(proc.returncode, command)
        except subprocess.CalledProcessError as e:
            label = context or "git command"
            detail = f"Command: {' '.join(str(c) for c in command)}\n{e}"
            raise RuntimeError(f"{label} failed.\n{detail}") from e

    def extend_description(self, dataset: str, local_clone: str):
        desc_path = os.path.join(local_clone, "dataset_description.json")
        with open(desc_path, "r") as f:
            description = json.load(f)

        # Normalize name using the argument provided
        description["Name"] = dataset

        # If the authors list is empty or missing, pull all collaborators as authors
        # if not description.get("Authors"):
        #     collaborators = repo.get_users_with_access()
        #     description["Authors"] = [c.id for c in collaborators]

        # If no keywords, add at least the dataset name
        if not description.get("Keywords"):
            description["Keywords"] = [dataset]

        # Add repository URL
        description["RepositoryURL"] = f"{self.client.url}/{self.organization.name}/{dataset}"
        # Add documentation link as AccessLink
        description["AccessInstructions"] = "Refer to the access link provided with the repository."
        description["AccessLink"] = "https://intranet.neuro.polymtl.ca/data/README.html"
        # Document all access as resctricted for now
        description["AccessType"] = "restricted"
        # Fetch repository maintainer as contact for access if not present
        # if "AccessEmail" not in description:
        #     users = repo.get_users_with_access()
        #     maintainers = repo.get_collaborators(role="maintainer")
        #     if maintainers.total > 0:
        #         description["AccessEmail"] = maintainers[0].email
        #     else:
        #         # Find owner then
        #         owners = repo.get_collaborators(role="owner")
        #         if owners.total > 0:
        #             description["AccessEmail"] = owners[0].email

        return description

    def download_subjects(
        self,
        subjects: list[tuple[str, str, str]],
        output_dir: Path,
        use_annex: bool = False,
    ) -> list[tuple[bool, str, str]]:
        """
        Download subject directories using authenticated sparse git clone.

        Multiple subjects that share the same repository are grouped together
        so that the repository is cloned exactly once, with all required sparse
        paths set in a single ``git sparse-checkout set`` call.  This avoids
        the "destination already exists" error and dramatically reduces network
        traffic for queries that return many subjects from one dataset.

        Args:
            subjects: Triples of ``(repo_url, sparse_path, dataset_name)``.
            output_dir: Base output directory.  Each dataset lands in
                        ``output_dir / dataset_name``.
            use_annex: When ``True``, run ``git annex get`` after each clone.

        Returns:
            List of ``(success, label, message)`` for each unique repository.
        """
        # Group sparse paths by (repo_url, dataset_name) so each repo is
        # cloned exactly once, regardless of how many subjects it contains.
        groups: dict[tuple[str, str], list[str]] = {}
        for repo_url, sparse_path, dataset_name in subjects:
            key = (repo_url, dataset_name)
            groups.setdefault(key, [])
            if sparse_path not in groups[key]:
                groups[key].append(sparse_path)

        results: list[tuple[bool, str, str]] = []

        for (repo_url, dataset_name), sparse_paths in groups.items():
            dest = output_dir / dataset_name
            label = f"{dataset_name} [{', '.join(sparse_paths)}]"

            try:
                self.clone_sparse(repo_url, sparse_paths, dest)
                if use_annex:
                    self.annex_get(dest, sparse_paths)
                results.append((True, label, "OK"))
            except RuntimeError as e:
                results.append((False, label, str(e)))

        return results


class BagelNeuroPolyMTL(BagelMixin, NeurobagelManager):
    def __init__(self, output_dir: str):
        NeurobagelManager.__init__(self, output_dir)
        BagelMixin.__init__(self, self.db)

    def convert_bids(
        self,
        dataset: str,
        bids_dir: str,
        phenotypes_tsv: str,
        phenotypes_annotations: str,
        dataset_description: dict,
        warnings_out: Optional[Dict] = None,
        extend_modalities: bool = False,
        extensions_config_path: Optional[str] = None,
        ai_client=None,
        validate_schema: bool = True,
    ):
        import csv as _csv
        from npdb.annotation.standardize import (
            fix_age_format,
            auto_add_missing_value_sentinels,
            fix_single_column_tsv,
            dedup_participant_ids,
            fill_empty_id_rows,
            fix_missing_levels,
        )

        # ── Phase 3: annotation pre-processing ─────────────────────────────
        preprocessing_warnings: List[str] = []
        pheno_tsv_path = Path(phenotypes_tsv)
        pheno_ann_path = Path(phenotypes_annotations)

        if pheno_tsv_path.exists() and pheno_ann_path.exists():
            preprocessing_warnings.extend(
                fix_single_column_tsv(pheno_tsv_path)
            )
            preprocessing_warnings.extend(
                dedup_participant_ids(pheno_tsv_path)
            )
            preprocessing_warnings.extend(
                fill_empty_id_rows(pheno_tsv_path)
            )
            preprocessing_warnings.extend(
                fix_age_format(pheno_tsv_path, pheno_ann_path)
            )
            preprocessing_warnings.extend(
                auto_add_missing_value_sentinels(
                    pheno_tsv_path, pheno_ann_path)
            )
            preprocessing_warnings.extend(
                fix_missing_levels(pheno_tsv_path, pheno_ann_path)
            )

        if warnings_out is not None:
            warnings_out["preprocessing_warnings"] = preprocessing_warnings

        # ── Phase 4: pre-flight checks ──────────────────────────────────────
        extra_suffix_map: Dict[str, str] = {}
        _vocab_extension_pending: List[str] = []
        if extend_modalities:
            from npdb.external.neurobagel.imaging_extensions import (
                build_extra_mapping,
                load_neuropoly_vocab,
                patch_bagel_suffix_map,
                _NIDM_ALIASES,
            )
            _config_path = Path(extensions_config_path) if extensions_config_path else (
                Path(__file__).resolve().parents[3] /
                "config" / "imaging_extensions.json"
            )
            _vocab_path = Path(__file__).resolve(
            ).parents[3] / "config" / "neuropoly_imaging_modalities.json"

            # Proactively build and apply the full known vocab before any
            # pre-flight check.  This ensures bagel is patched regardless of
            # whether check_bids_suffixes raises (it won't for TIFF-only
            # datasets or mixed datasets where ≥1 standard suffix is present).
            _neuropoly_vocab = load_neuropoly_vocab(_vocab_path)
            for _abbr, (_iri, _name) in _neuropoly_vocab.items():
                extra_suffix_map[_abbr] = _iri
            for _abbr, (_iri, _desc) in _NIDM_ALIASES.items():
                extra_suffix_map.setdefault(_abbr, _iri)
            patch_bagel_suffix_map(extra_suffix_map)

            # Second pass: discover any suffixes that are STILL unsupported
            # (not in the neuropoly vocab or NIDM aliases) and resolve them
            # via the extensions cache, LLM, or generic fallback.
            try:
                check_bids_suffixes(
                    bids_dir, extra_suffix_map=extra_suffix_map)
            except PreflightError as _pf:
                import re as _re
                _m = _re.search(r"\[([^\]]+)\]", _pf.description)
                if _m:
                    _raw = _m.group(1)
                    _suffixes = [s.strip().strip("'")
                                 for s in _raw.split(",") if s.strip()]
                    _extra, _ext_warnings = build_extra_mapping(
                        _suffixes, _config_path, ai_client, _vocab_path
                    )
                    extra_suffix_map.update(_extra)
                    for w in _ext_warnings:
                        if w.startswith("vocab_extension_pending:"):
                            _vocab_extension_pending.append(w)
                        else:
                            preprocessing_warnings.append(w)
                    patch_bagel_suffix_map(extra_suffix_map)
                    if warnings_out is not None:
                        warnings_out["preprocessing_warnings"] = preprocessing_warnings

        check_bids_suffixes(bids_dir, extra_suffix_map=extra_suffix_map)

        # Generate TSV from BIDS directory
        with tempfile.NamedTemporaryFile(suffix=".tsv", mode='w+', delete=False) as tmp_file:
            with tempfile.NamedTemporaryFile(suffix=".json", mode='w+', delete=False) as tmp_desc:
                json.dump(dataset_description, tmp_desc)
                tmp_desc.flush()

                self.bids2tsv(bids_directory=bids_dir,
                              output_tsv=tmp_file.name)

                # Generate JSON-LD from TSV and phenotypes description
                # ── Phase 5a: schema pre-validation ──────────────────────────
                if validate_schema and pheno_ann_path.exists():
                    with open(pheno_ann_path, "r", encoding="utf-8") as _fh:
                        _ann_schema = json.load(_fh)
                    _schema_errors: List[str] = []
                    for _col, _col_data in _ann_schema.items():
                        _ann = _col_data.get("Annotations", {})
                        if not _ann:
                            _schema_errors.append(
                                f"Column '{_col}' is missing the 'Annotations' block."
                            )
                            continue
                        _is_about = _ann.get("IsAbout", {})
                        if not _is_about.get("TermURL"):
                            _schema_errors.append(
                                f"Column '{_col}': Annotations.IsAbout.TermURL is missing or empty."
                            )
                    if _schema_errors:
                        raise BagelCLIError.from_result(
                            command="schema pre-validation",
                            exit_code=1,
                            output="\n".join(
                                ["not a valid Neurobagel data dictionary — pre-validation failed:"]
                                + _schema_errors
                            ),
                        )

                self.bagel_pheno(
                    dataset_name=dataset,
                    phenotypes_tsv=phenotypes_tsv,
                    phenotypes_annotations=phenotypes_annotations,
                    dataset_description=tmp_desc.name
                )

                # ── Phase 5: case-insensitive subject alignment ──────────────
                subject_alignment_warnings: List[str] = []
                jsonld_path = os.path.join(self.db.root, f"{dataset}.jsonld")
                bids_tsv_path = tmp_file.name

                # Extract subjects present in the freshly-generated JSON-LD
                jsonld_subjects: Set[str] = set()
                try:
                    with open(jsonld_path, "r", encoding="utf-8") as jf:
                        jsonld_data = json.load(jf)
                    for sample in jsonld_data.get("hasSamples", []):
                        label = sample.get("hasLabel", "").strip().lower()
                        if label:
                            jsonld_subjects.add(label)
                except (OSError, json.JSONDecodeError, KeyError):
                    pass

                if jsonld_subjects:
                    # Build filtered BIDS TSV containing only JSON-LD subjects
                    filtered_rows: List[Dict] = []
                    discarded: List[str] = []
                    with open(bids_tsv_path, "r", encoding="utf-8", newline="") as fh:
                        reader = _csv.DictReader(fh, delimiter="\t")
                        fieldnames = reader.fieldnames or []
                        for row in reader:
                            # bids2tsv outputs a "sub" column (not "participant_id")
                            pid = row.get("sub", "").strip().lower()
                            if pid in jsonld_subjects:
                                filtered_rows.append(row)
                            else:
                                discarded.append(
                                    row.get("sub", pid))

                    if discarded:
                        subject_alignment_warnings.extend(
                            [f"Subject '{s}' in BIDS TSV absent from JSON-LD; excluded from bagel bids."
                             for s in discarded]
                        )

                    if not filtered_rows:
                        subject_alignment_warnings.append(
                            "All BIDS TSV subjects are absent from the JSON-LD; skipping bagel bids."
                        )
                    else:
                        # Write filtered BIDS TSV to a temp file
                        with tempfile.NamedTemporaryFile(
                            suffix=".tsv", mode="w", delete=False, encoding="utf-8"
                        ) as filtered_tmp:
                            writer = _csv.DictWriter(
                                filtered_tmp, fieldnames=fieldnames,
                                delimiter="\t", lineterminator="\n"
                            )
                            writer.writeheader()
                            writer.writerows(filtered_rows)
                            filtered_tmp_path = filtered_tmp.name

                        try:
                            self.bagel_bids(
                                dataset_name=dataset,
                                bids_table=filtered_tmp_path,
                            )
                        finally:
                            try:
                                os.unlink(filtered_tmp_path)
                            except OSError:
                                pass
                else:
                    # No JSON-LD subjects could be read — run bagel bids with original TSV
                    self.bagel_bids(
                        dataset_name=dataset,
                        bids_table=tmp_file.name,
                    )

                if warnings_out is not None:
                    warnings_out["subject_alignment_warnings"] = subject_alignment_warnings
                    if _vocab_extension_pending:
                        warnings_out["vocab_extension_pending"] = _vocab_extension_pending

        # Clean up temp files
        try:
            os.unlink(tmp_file.name)
        except OSError:
            pass
        try:
            os.unlink(tmp_desc.name)
        except OSError:
            pass
