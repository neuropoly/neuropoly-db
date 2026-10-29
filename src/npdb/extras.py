"""Optional dependency guards for provider backends and automation."""

from __future__ import annotations

import importlib
import shutil
from types import ModuleType

from npdb.errors import MissingDependencyError

# No more extras in the project for now, we install everything by default.
# Could be reinstated if PEP771 is integrated : https://peps.python.org/pep-0771/
#
# INSTALL_HINT = (
#     "pip install 'npdb[{extra}]'  "
#     "(uv tool: uv tool install --reinstall 'npdb[{extra}] @ git+https://github.com/neuropoly/neuropoly-db'; "
#     "checkout: uv sync --extra {extra})"
# )
#
#
# class MissingExtraError(MissingDependencyError, ImportError):
#     """An optional extra needed by the requested feature is not installed."""
#
#     def __init__(self, feature: str, extra: str):
#         self.feature = feature
#         self.extra = extra
#         super().__init__(
#             f"{feature} requires the '{extra}' extra.",
#             hint=INSTALL_HINT.format(extra=extra),
#             docs="Development-Environment-Setup#dependency-extras-and-groups",
#         )


class MissingExecutableError(MissingDependencyError, RuntimeError):
    """A system executable needed by the requested feature is not on PATH."""

    def __init__(self, feature: str, executable: str, hint: str = ""):
        self.feature = feature
        self.executable = executable
        super().__init__(
            f"{feature} requires '{executable}' on PATH.",
            hint=hint or None,
            docs="Development-Environment-Setup#native-tools",
        )


# def require_module(module: str, *, extra: str, feature: str) -> ModuleType:
#     """Import an optional module, or raise MissingExtraError naming the extra."""
#     try:
#         return importlib.import_module(module)
#     except ImportError as exc:
#         raise MissingExtraError(feature, extra) from exc


def require_executable(executable: str, *, feature: str, hint: str = "") -> str:
    """Return the path of a system executable, or raise MissingExecutableError."""
    path = shutil.which(executable)
    if path is None:
        raise MissingExecutableError(feature, executable, hint)
    return path
