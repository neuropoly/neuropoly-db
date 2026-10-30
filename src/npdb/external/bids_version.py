"""Deprecated import alias; use bids_repair.bids_version instead."""

import sys
import warnings

from bids_repair import bids_version

warnings.warn(__doc__, DeprecationWarning, stacklevel=2)
sys.modules[__name__] = bids_version
