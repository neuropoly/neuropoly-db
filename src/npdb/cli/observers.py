"""
Observer protocol for download progress in npdb CLI commands.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class DownloadObserver(Protocol):
    """
    Observer for repository download operations.

    Register via
    :meth:`~npdb.external.neurodata.gitea.GiteaManager.add_download_observer`.
    All methods are called from the thread that runs the git subprocess.
    """

    def on_repo_step(
        self, repo: str, step: str, step_num: int, total_steps: int
    ) -> None:
        """Called when a new step begins for *repo* (clone, sparse-checkout, …)."""
        ...

    def on_file_progress(
        self, repo: str, file: str, bytes_done: int, bytes_total: int
    ) -> None:
        """Called repeatedly with byte-level download progress for a *file* in *repo*."""
        ...

    def on_file_complete(self, repo: str, file: str) -> None:
        """Called once when *file* inside *repo* has been fully downloaded."""
        ...

    def on_repo_done(self, repo: str, success: bool) -> None:
        """Called when all operations for *repo* have completed or failed."""
        ...

    def on_repo_error(self, repo: str, message: str) -> None:
        """Called when an error occurs while processing *repo*."""
        ...
