"""
Tests for progress callback functionality in download operations.

Tests the _run_git progress_callback, clone_sparse step_callback, and
download_subjects callback propagation features added for real-time progress display.
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from npdb.managers.neuropoly import DataNeuroPolyMTL


@pytest.fixture()
def manager(tmp_path):
    """A DataNeuroPolyMTL instance with all network calls mocked."""
    with (
        patch("npdb.external.neurogitea.gitea.gt_client.Gitea") as MockGitea,
        patch("npdb.managers.neurogitea.OrganizationMixin.__init__", return_value=None),
    ):
        mock_client = MagicMock()
        mock_client.requests.verify = False
        MockGitea.return_value = mock_client
        mgr = DataNeuroPolyMTL(
            url="https://data.neuro.polymtl.ca",
            user="testuser",
            token="testtoken",
            ssl_verify=False,
        )
        yield mgr


# ---------------------------------------------------------------------------
# GiteaManager — _run_git with progress_callback
# ---------------------------------------------------------------------------


class TestRunGitProgress:
    """_run_git parses JSON progress events from stdout when progress_callback is given."""

    def test_progress_callback_called_for_percent_done_events(self, manager, tmp_path):
        """Progress events with 'percentdone' trigger progress_callback."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        callback = Mock()

        # Simulate git annex get --json --json-progress output
        json_events = [
            json.dumps(
                {
                    "action": {"file": "file1.nii.gz"},
                    "percentdone": 50,
                    "bytesdone": 1000,
                    "bytestotal": 2000,
                }
            ),
            json.dumps(
                {
                    "action": {"file": "file1.nii.gz"},
                    "percentdone": 100,
                    "bytesdone": 2000,
                    "bytestotal": 2000,
                }
            ),
        ]
        stdout_data = "\n".join(json_events) + "\n"

        mock_result = MagicMock()
        mock_result.stdout = stdout_data
        mock_result.returncode = 0

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = mock_result
            manager._run_git(
                [
                    "git",
                    "-C",
                    str(repo_dir),
                    "annex",
                    "get",
                    "--json",
                    "--json-progress",
                ],
                {},
                context=f"git annex get in '{repo_dir}'",
                progress_callback=callback,
            )

        # Verify callback was called twice (once for 50%, once for 100%)
        assert callback.call_count == 2
        first_call = callback.call_args_list[0]
        assert first_call[0] == ("file1.nii.gz", 50.0, 1000, 2000)

    def test_completion_events_trigger_100_percent(self, manager, tmp_path):
        """Completion events with 'success': true trigger 100% callback."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        callback = Mock()

        json_events = [
            json.dumps(
                {
                    "command": "get",
                    "success": True,
                    "file": "file1.nii.gz",
                }
            ),
        ]
        stdout_data = "\n".join(json_events) + "\n"

        mock_result = MagicMock()
        mock_result.stdout = stdout_data
        mock_result.returncode = 0

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = mock_result
            manager._run_git(
                [
                    "git",
                    "-C",
                    str(repo_dir),
                    "annex",
                    "get",
                    "--json",
                    "--json-progress",
                ],
                {},
                context=f"git annex get in '{repo_dir}'",
                progress_callback=callback,
            )

        # Verify callback was called with 100.0
        assert callback.call_count == 1
        assert callback.call_args[0] == ("file1.nii.gz", 100.0, 0, 0)

    def test_malformed_json_lines_skipped(self, manager, tmp_path):
        """Malformed JSON lines are silently skipped."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        callback = Mock()

        lines = [
            "not json at all",
            json.dumps(
                {
                    "action": {"file": "file1.nii.gz"},
                    "percentdone": 50,
                    "bytesdone": 100,
                    "bytestotal": 200,
                }
            ),
            "{ broken json",
            "",
        ]
        stdout_data = "\n".join(lines) + "\n"

        mock_result = MagicMock()
        mock_result.stdout = stdout_data
        mock_result.returncode = 0

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = mock_result
            manager._run_git(
                [
                    "git",
                    "-C",
                    str(repo_dir),
                    "annex",
                    "get",
                    "--json",
                    "--json-progress",
                ],
                {},
                context=f"git annex get in '{repo_dir}'",
                progress_callback=callback,
            )

        # Only one valid JSON event should trigger callback
        assert callback.call_count == 1

    def test_non_zero_returncode_raises_error(self, manager, tmp_path):
        """Process exit code != 0 raises RuntimeError."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        callback = Mock()

        cmd = [
            "git",
            "-C",
            str(repo_dir),
            "annex",
            "get",
            "--json",
            "--json-progress",
        ]

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                1, cmd, output="", stderr="fatal: error message"
            )
            with pytest.raises(RuntimeError, match="git annex get.*failed"):
                manager._run_git(
                    cmd,
                    {},
                    context=f"git annex get in '{repo_dir}'",
                    progress_callback=callback,
                )


# ---------------------------------------------------------------------------
# GiteaManager — clone_sparse with step_callback
# ---------------------------------------------------------------------------


class TestCloneSparseProgress:
    """clone_sparse calls step_callback before each git operation."""

    def test_step_callback_called_before_clone(self, manager, tmp_path):
        """step_callback is called with 'Cloning ...' message."""
        dest = tmp_path / "repo"
        callback = Mock()

        with patch.object(manager, "_run_git"):
            manager.clone_sparse(
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                ["sub-amuAP"],
                dest,
                step_callback=callback,
            )

        # First call should be for cloning
        first_call = callback.call_args_list[0]
        assert "Cloning" in first_call[0][0]
        assert "whole-spine" in first_call[0][0]

    def test_step_callback_called_before_sparse_checkout_init(self, manager, tmp_path):
        """step_callback is called with 'Initializing sparse checkout...' message."""
        dest = tmp_path / "repo"
        callback = Mock()

        with patch.object(manager, "_run_git"):
            manager.clone_sparse(
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                ["sub-amuAP"],
                dest,
                step_callback=callback,
            )

        calls_text = [c[0][0] for c in callback.call_args_list]
        assert any("Initializing" in t for t in calls_text)

    def test_step_callback_called_before_sparse_checkout_set(self, manager, tmp_path):
        """step_callback is called with path configuration message."""
        dest = tmp_path / "repo"
        callback = Mock()

        with patch.object(manager, "_run_git"):
            manager.clone_sparse(
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                ["sub-amuAP", "sub-amuLJ"],
                dest,
                step_callback=callback,
            )

        calls_text = [c[0][0] for c in callback.call_args_list]
        assert any("Configuring paths" in t for t in calls_text)
        assert any("sub-amuAP" in t for t in calls_text)

    def test_step_callback_called_before_checkout(self, manager, tmp_path):
        """step_callback is called with 'Checking out files...' message."""
        dest = tmp_path / "repo"
        callback = Mock()

        with patch.object(manager, "_run_git"):
            manager.clone_sparse(
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                ["sub-amuAP"],
                dest,
                step_callback=callback,
            )

        calls_text = [c[0][0] for c in callback.call_args_list]
        assert any("Checking out" in t for t in calls_text)

    def test_step_callback_not_required(self, manager, tmp_path):
        """clone_sparse works without step_callback (backward compatible)."""
        dest = tmp_path / "repo"

        with patch.object(manager, "_run_git"):
            # Should not raise when callback is None
            manager.clone_sparse(
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                ["sub-amuAP"],
                dest,
                step_callback=None,
            )

    def test_step_callback_skipped_when_clone_already_exists(self, manager, tmp_path):
        """step_callback for clone is not called if .git already exists."""
        dest = tmp_path / "repo"
        dest.mkdir()
        (dest / ".git").mkdir()

        callback = Mock()

        with patch.object(manager, "_run_git"):
            manager.clone_sparse(
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                ["sub-amuAP"],
                dest,
                step_callback=callback,
            )

        calls_text = [c[0][0] for c in callback.call_args_list]
        # Should not have "Cloning" in the calls (only init, set, checkout)
        assert not any("Cloning" in t for t in calls_text)
        assert any("Initializing" in t for t in calls_text)


# ---------------------------------------------------------------------------
# DataNeuroPolyMTL — download_subjects with callbacks
# ---------------------------------------------------------------------------


class TestDownloadSubjectsCallbacks:
    """download_subjects propagates callbacks to clone_sparse and annex_get."""

    def test_git_step_callback_passed_to_clone_sparse(self, tmp_path):
        """git_step_callback is passed to clone_sparse."""
        with (
            patch("npdb.external.neurogitea.gitea.gt_client.Gitea") as MockGitea,
            patch(
                "npdb.managers.neurogitea.OrganizationMixin.__init__", return_value=None
            ),
        ):
            mock_client = MagicMock()
            mock_client.requests.verify = False
            MockGitea.return_value = mock_client
            dnp = DataNeuroPolyMTL(
                url="https://data.neuro.polymtl.ca",
                user="testuser",
                token="testtoken",
                ssl_verify=False,
            )

            callback = Mock()
            subjects = [
                (
                    "https://data.neuro.polymtl.ca/datasets/whole-spine",
                    "sub-amuAP",
                    "whole-spine",
                ),
            ]

            with patch.object(dnp, "clone_sparse") as mock_clone:
                dnp.download_subjects(
                    subjects, tmp_path, use_annex=False, git_step_callback=callback
                )

            # Verify clone_sparse was called with the callback
            mock_clone.assert_called_once()
            call_kwargs = mock_clone.call_args.kwargs
            assert call_kwargs.get("step_callback") is callback

    def test_annex_progress_callback_passed_to_annex_get(self, tmp_path):
        """annex_progress_callback is passed to annex_get."""
        with (
            patch("npdb.external.neurogitea.gitea.gt_client.Gitea") as MockGitea,
            patch(
                "npdb.managers.neurogitea.OrganizationMixin.__init__", return_value=None
            ),
        ):
            mock_client = MagicMock()
            mock_client.requests.verify = False
            MockGitea.return_value = mock_client
            dnp = DataNeuroPolyMTL(
                url="https://data.neuro.polymtl.ca",
                user="testuser",
                token="testtoken",
                ssl_verify=False,
            )

            callback = Mock()
            subjects = [
                (
                    "https://data.neuro.polymtl.ca/datasets/whole-spine",
                    "sub-amuAP",
                    "whole-spine",
                ),
            ]

            with (
                patch.object(dnp, "clone_sparse"),
                patch.object(dnp, "annex_get") as mock_annex,
            ):
                dnp.download_subjects(
                    subjects, tmp_path, use_annex=True, annex_progress_callback=callback
                )

            # Verify annex_get was called with the callback
            mock_annex.assert_called_once()
            call_kwargs = mock_annex.call_args.kwargs
            assert call_kwargs.get("progress_callback") is callback

    def test_both_callbacks_passed(self, tmp_path):
        """Both git_step_callback and annex_progress_callback can be passed."""
        with (
            patch("npdb.external.neurogitea.gitea.gt_client.Gitea") as MockGitea,
            patch(
                "npdb.managers.neurogitea.OrganizationMixin.__init__", return_value=None
            ),
        ):
            mock_client = MagicMock()
            mock_client.requests.verify = False
            MockGitea.return_value = mock_client
            dnp = DataNeuroPolyMTL(
                url="https://data.neuro.polymtl.ca",
                user="testuser",
                token="testtoken",
                ssl_verify=False,
            )

            step_callback = Mock()
            progress_callback = Mock()
            subjects = [
                (
                    "https://data.neuro.polymtl.ca/datasets/whole-spine",
                    "sub-amuAP",
                    "whole-spine",
                ),
            ]

            with (
                patch.object(dnp, "clone_sparse") as mock_clone,
                patch.object(dnp, "annex_get") as mock_annex,
            ):
                dnp.download_subjects(
                    subjects,
                    tmp_path,
                    use_annex=True,
                    git_step_callback=step_callback,
                    annex_progress_callback=progress_callback,
                )

            mock_clone.assert_called_once()
            assert mock_clone.call_args.kwargs.get("step_callback") is step_callback

            mock_annex.assert_called_once()
            assert (
                mock_annex.call_args.kwargs.get("progress_callback")
                is progress_callback
            )

    def test_callbacks_not_required_backward_compatible(self, tmp_path):
        """download_subjects works without callbacks (backward compatible)."""
        with (
            patch("npdb.external.neurogitea.gitea.gt_client.Gitea") as MockGitea,
            patch(
                "npdb.managers.neurogitea.OrganizationMixin.__init__", return_value=None
            ),
        ):
            mock_client = MagicMock()
            mock_client.requests.verify = False
            MockGitea.return_value = mock_client
            dnp = DataNeuroPolyMTL(
                url="https://data.neuro.polymtl.ca",
                user="testuser",
                token="testtoken",
                ssl_verify=False,
            )

            subjects = [
                (
                    "https://data.neuro.polymtl.ca/datasets/whole-spine",
                    "sub-amuAP",
                    "whole-spine",
                ),
            ]

            with patch.object(dnp, "clone_sparse"), patch.object(dnp, "annex_get"):
                # Should not raise
                results = dnp.download_subjects(subjects, tmp_path, use_annex=True)

            assert len(results) == 1
