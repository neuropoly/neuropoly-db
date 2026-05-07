from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, field_validator


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

    mode: str = Field(default="manual", description="Execution mode")

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, v: str) -> str:
        valid = {m.value for m in AnnotationMode}
        if v not in valid:
            raise ValueError(f"mode must be one of {sorted(valid)}, got {v!r}")
        return v

    headless: bool = Field(default=True, description="Run browser in headless mode")
    timeout: int = Field(
        default=300,
        description="Timeout per automation step (seconds). Applies to each operation with retry.",
    )
    artifacts_dir: Optional[Path] = Field(
        default=None,
        description="Directory for screenshots/traces on failure. Auto-created if provided.",
    )
    ai_provider: Optional[str] = Field(
        default=None, description="AI provider (e.g., 'ollama')"
    )
    ai_model: Optional[str] = Field(
        default=None, description="AI model name (e.g., 'neural-chat')"
    )
    phenotype_dictionary: Optional[Path] = Field(
        default=None, description="Optional user-supplied phenotype dictionary JSON"
    )
    dry_run: bool = Field(
        default=False, description="Print changes to terminal without writing files"
    )
    keep_annotations: bool = Field(
        default=False,
        description="Include Neurobagel Annotations block in participants.json output",
    )
    header_map: Optional[Path] = Field(
        default=None,
        description="JSON file mapping desired output headers to lists of input variants",
    )
    no_new_columns: bool = Field(
        default=False, description="Don't add missing standard columns (e.g., age, sex)"
    )
