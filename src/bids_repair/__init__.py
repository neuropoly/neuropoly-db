"""Provider-independent validation and repair of local BIDS-like datasets."""

from bids_repair.dataset import (
    find_dataset_root,
    prepare_dataset,
    prepare_provider_dataset,
)
from bids_repair.description import read_dataset_description, update_dataset_description
from bids_repair.repair import (
    BidsRepairError,
    apply_bids_repair,
    format_bids_repair_plan,
    plan_bids_repair,
    recover_bids_repair,
)
from bids_repair.sidecars import build_participants_sidecar, validate_bids_sidecar
from bids_repair.standardize import standardize_dataset
from bids_repair.validation import (
    BidsSeverity,
    BidsValidationError,
    get_bids_validation_report,
    validate_and_repair_bids_dataset,
    validate_bids_dataset,
)

__all__ = [
    "BidsRepairError",
    "BidsSeverity",
    "BidsValidationError",
    "apply_bids_repair",
    "format_bids_repair_plan",
    "get_bids_validation_report",
    "plan_bids_repair",
    "recover_bids_repair",
    "validate_and_repair_bids_dataset",
    "validate_bids_dataset",
    "find_dataset_root",
    "prepare_provider_dataset",
    "prepare_dataset",
    "standardize_dataset",
    "build_participants_sidecar",
    "validate_bids_sidecar",
    "read_dataset_description",
    "update_dataset_description",
]
