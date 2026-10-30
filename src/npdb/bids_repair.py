"""Deprecated import alias; use bids_repair.repair instead."""

import sys
import warnings

from bids_repair import repair

warnings.warn(__doc__, DeprecationWarning, stacklevel=2)
sys.modules[__name__] = repair
