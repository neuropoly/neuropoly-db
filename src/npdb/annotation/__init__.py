from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class AnnotationMode(str, Enum):
    MANUAL = "manual"
    ASSIST = "assist"
    AUTO = "auto"
    FULL_AUTO = "full-auto"


class AnnotationConfig(BaseModel):
    """
    Configuration for annotation automation.

    Timeout Semantics:
    - Applied PER OPERATION, not total runtime (upload, click, wait, etc.)
    - Each browser interaction may take up to this timeout
    - Retry logic means an operation can take up to N_ATTEMPTS * timeout
    - Default 300s (5 min) per operation is typical for slow networks

    For slow networks or large files, increase timeout:
    - Normal: 300s (default)
    - Slow network: 600s (10 min)
    - Very slow/large files: 1200s (20 min)
    """

    mode: AnnotationMode = Field(
        default=AnnotationMode.MANUAL, description="Execution mode"
    )
    headless: bool = Field(default=True, description="Run browser in headless mode")
    timeout: int = Field(
        default=300,
        description="Timeout per automation step (seconds). Applies to each operation with retry.",
    )
    artifacts_dir: Path | None = Field(
        default=None,
        description="Directory for screenshots/traces on failure. Auto-created if provided.",
    )
    ai_provider: str | None = Field(
        default=None, description="AI provider (e.g., 'ollama')"
    )
    ai_model: str | None = Field(
        default=None, description="AI model name (e.g., 'neural-chat')"
    )
    phenotype_dictionary: Path | None = Field(
        default=None, description="Optional user-supplied phenotype dictionary JSON"
    )
    dry_run: bool = Field(
        default=False, description="Print changes to terminal without writing files"
    )
    keep_annotations: bool = Field(
        default=False,
        description="Include Neurobagel Annotations block in participants.json output",
    )
    header_map: Path | None = Field(
        default=None,
        description="JSON file mapping desired output headers to lists of input variants",
    )
    no_new_columns: bool = Field(
        default=False, description="Don't add missing standard columns (e.g., age, sex)"
    )
