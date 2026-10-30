"""Deprecated import alias; use bids_repair.validation instead."""

import sys
import warnings

from bids_repair import validation

warnings.warn(__doc__, DeprecationWarning, stacklevel=2)
sys.modules[__name__] = validation
