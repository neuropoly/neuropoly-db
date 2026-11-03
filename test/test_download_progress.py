"""
Tests for the DownloadObserver-based progress notification system.

Covers _run_git JSON event parsing, clone_sparse step notifications,
and download_subjects on_repo_done notifications.
"""

import io
import json
from unittest.mock import MagicMock, patch

import pytest

from npdb.cli.observers import DownloadObserver
from npdb.managers.neuropoly import DataNeuroPolyMTL


@pytest.fixture()
def manager(tmp_path):
    """A DataNeuroPolyMTL instance with all network calls mocked."""
    with (
        patch("npdb.external.neurodata.gitea.gt_client.Gitea") as MockGitea,
        patch("npdb.managers.neurodata.OrganizationMixin.__init__", return_value=None),
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


@pytest.fixture()
def observer():
    """A mock DownloadObserver."""
    return MagicMock(spec=DownloadObserver)


class TestRunGitProgress:
    """_run_git notifies observers with typed file events when repo_name is set."""

    def test_on_file_progress_called_for_percent_done_events(
        self, manager, observer, tmp_path
    ):
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        manager.add_download_observer(observer)

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
        popen_proc = MagicMock()
        popen_proc.stdout = io.StringIO("\n".join(json_events) + "\n")
        popen_proc.stderr = io.StringIO("")
        popen_proc.wait.return_value = 0

        with patch("subprocess.Popen", return_value=popen_proc):
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
                context="test",
                line_parser_hook=lambda line: manager._parse_annex_event_line(
                    "whole-spine", line
                ),
            )

        assert observer.on_file_progress.call_count == 2
        observer.on_file_progress.assert_any_call(
            "whole-spine", "file1.nii.gz", 1000, 2000
        )
        observer.on_file_complete.assert_not_called()

    def test_on_file_complete_called_for_success_events(
        self, manager, observer, tmp_path
    ):
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        manager.add_download_observer(observer)

        json_events = [
            json.dumps({"command": "get", "success": True, "file": "file1.nii.gz"})
        ]
        popen_proc = MagicMock()
        popen_proc.stdout = io.StringIO("\n".join(json_events) + "\n")
        popen_proc.stderr = io.StringIO("")
        popen_proc.wait.return_value = 0

        with patch("subprocess.Popen", return_value=popen_proc):
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
                context="test",
                line_parser_hook=lambda line: manager._parse_annex_event_line(
                    "whole-spine", line
                ),
            )

        observer.on_file_complete.assert_called_once_with("whole-spine", "file1.nii.gz")
        observer.on_file_progress.assert_not_called()

    def test_no_file_events_when_repo_name_is_none(self, manager, observer, tmp_path):
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        manager.add_download_observer(observer)

        json_events = [
            json.dumps(
                {
                    "action": {"file": "file1.nii.gz"},
                    "percentdone": 50,
                    "bytesdone": 100,
                    "bytestotal": 200,
                }
            ),
        ]
        popen_proc = MagicMock()
        popen_proc.stdout = io.StringIO("\n".join(json_events) + "\n")
        popen_proc.stderr = io.StringIO("")
        popen_proc.wait.return_value = 0

        with patch("subprocess.Popen", return_value=popen_proc):
            manager._run_git(["git", "clone", "..."], {}, context="test")

        observer.on_file_progress.assert_not_called()
        observer.on_file_complete.assert_not_called()

    def test_malformed_json_lines_skipped(self, manager, observer, tmp_path):
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        manager.add_download_observer(observer)

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
        popen_proc = MagicMock()
        popen_proc.stdout = io.StringIO("\n".join(lines) + "\n")
        popen_proc.stderr = io.StringIO("")
        popen_proc.wait.return_value = 0

        with patch("subprocess.Popen", return_value=popen_proc):
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
                context="test",
                line_parser_hook=lambda line: manager._parse_annex_event_line(
                    "whole-spine", line
                ),
            )

        assert observer.on_file_progress.call_count == 1

    def test_non_zero_returncode_raises_error(self, manager, tmp_path):
        cmd = ["git", "-C", str(tmp_path), "annex", "get", "--json", "--json-progress"]

        popen_proc = MagicMock()
        popen_proc.stdout = io.StringIO("")
        popen_proc.stderr = io.StringIO("fatal: error message\n")
        popen_proc.wait.return_value = 1

        with patch("subprocess.Popen", return_value=popen_proc):
            with pytest.raises(RuntimeError, match="git annex get.*failed"):
                manager._run_git(
                    cmd,
                    {},
                    context=f"git annex get in '{tmp_path}'",
                    line_parser_hook=lambda line: manager._parse_annex_event_line(
                        "whole-spine", line
                    ),
                )

    def test_progress_events_from_stderr_are_parsed(self, manager, observer, tmp_path):
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        manager.add_download_observer(observer)

        stderr_events = [
            json.dumps(
                {
                    "action": {"file": "file2.nii.gz"},
                    "percentdone": 25,
                    "bytesdone": 250,
                    "bytestotal": 1000,
                }
            ),
            json.dumps({"command": "get", "success": True, "file": "file2.nii.gz"}),
        ]

        popen_proc = MagicMock()
        popen_proc.stdout = io.StringIO("")
        popen_proc.stderr = io.StringIO("\n".join(stderr_events) + "\n")
        popen_proc.wait.return_value = 0

        with patch("subprocess.Popen", return_value=popen_proc):
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
                context="test",
                line_parser_hook=lambda line: manager._parse_annex_event_line(
                    "whole-spine", line
                ),
            )

        observer.on_file_progress.assert_called_with(
            "whole-spine", "file2.nii.gz", 250, 1000
        )
        observer.on_file_complete.assert_called_with("whole-spine", "file2.nii.gz")


class TestCloneSparseProgress:
    """clone_sparse calls on_repo_step before each git operation."""

    def test_repo_step_fired_for_clone(self, manager, observer, tmp_path):
        dest = tmp_path / "repo"
        manager.add_download_observer(observer)

        with patch.object(manager, "_run_git"):
            manager.clone_sparse(
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                ["sub-amuAP"],
                dest,
            )

        labels = [c.args[1] for c in observer.on_repo_step.call_args_list]
        assert any("Cloning" in lbl for lbl in labels)
        assert any("whole-spine" in lbl for lbl in labels)

    def test_repo_name_propagated_to_all_steps(self, manager, observer, tmp_path):
        dest = tmp_path / "repo"
        manager.add_download_observer(observer)

        with patch.object(manager, "_run_git"):
            manager.clone_sparse(
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                ["sub-amuAP"],
                dest,
            )

        repos = {c.args[0] for c in observer.on_repo_step.call_args_list}
        assert repos == {"whole-spine"}

    def test_sparse_checkout_steps_notified(self, manager, observer, tmp_path):
        dest = tmp_path / "repo"
        manager.add_download_observer(observer)

        with patch.object(manager, "_run_git"):
            manager.clone_sparse(
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                ["sub-amuAP"],
                dest,
            )

        labels = [c.args[1] for c in observer.on_repo_step.call_args_list]
        assert any("sparse" in lbl.lower() for lbl in labels)
        assert any("checkout" in lbl.lower() for lbl in labels)

    def test_clone_step_skipped_when_git_dir_exists(self, manager, observer, tmp_path):
        dest = tmp_path / "repo"
        dest.mkdir()
        (dest / ".git").mkdir()
        manager.add_download_observer(observer)

        with patch.object(manager, "_run_git"):
            manager.clone_sparse(
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                ["sub-amuAP"],
                dest,
            )

        labels = [c.args[1] for c in observer.on_repo_step.call_args_list]
        assert not any("Cloning" in lbl for lbl in labels)

    def test_no_observer_required(self, manager, tmp_path):
        dest = tmp_path / "repo"
        with patch.object(manager, "_run_git"):
            manager.clone_sparse(
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                ["sub-amuAP"],
                dest,
            )


class TestDownloadSubjectsObserver:
    """download_subjects fires on_repo_done for each repository processed."""

    def _make_manager(self):
        with (
            patch("npdb.external.neurodata.gitea.gt_client.Gitea") as MockGitea,
            patch(
                "npdb.managers.neurodata.OrganizationMixin.__init__", return_value=None
            ),
        ):
            mock_client = MagicMock()
            mock_client.requests.verify = False
            MockGitea.return_value = mock_client
            return DataNeuroPolyMTL(
                url="https://data.neuro.polymtl.ca",
                user="testuser",
                token="testtoken",
                ssl_verify=False,
            )

    def test_on_repo_done_success_fired(self, tmp_path):
        dnp = self._make_manager()
        obs = MagicMock(spec=DownloadObserver)
        dnp.add_download_observer(obs)
        subjects = [
            (
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                "sub-amuAP",
                "whole-spine",
            )
        ]

        with patch.object(dnp, "clone_sparse"):
            dnp.download_subjects(subjects, tmp_path, use_annex=False)

        obs.on_repo_done.assert_called_once_with("whole-spine", True)

    def test_on_repo_done_failure_fired_on_error(self, tmp_path):
        dnp = self._make_manager()
        obs = MagicMock(spec=DownloadObserver)
        dnp.add_download_observer(obs)
        subjects = [
            (
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                "sub-amuAP",
                "whole-spine",
            )
        ]

        with patch.object(dnp, "clone_sparse", side_effect=RuntimeError("clone boom")):
            results = dnp.download_subjects(subjects, tmp_path, use_annex=False)

        obs.on_repo_done.assert_called_once_with("whole-spine", False)
        assert results[0][0] is False

    def test_on_repo_error_fired_on_error(self, tmp_path):
        dnp = self._make_manager()
        obs = MagicMock(spec=DownloadObserver)
        dnp.add_download_observer(obs)
        subjects = [
            (
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                "sub-amuAP",
                "whole-spine",
            )
        ]

        with patch.object(dnp, "clone_sparse", side_effect=RuntimeError("clone boom")):
            dnp.download_subjects(subjects, tmp_path, use_annex=False)

        obs.on_repo_error.assert_called_once_with("whole-spine", "clone boom")

    def test_on_repo_done_fired_per_unique_repo(self, tmp_path):
        dnp = self._make_manager()
        obs = MagicMock(spec=DownloadObserver)
        dnp.add_download_observer(obs)
        subjects = [
            (
                "https://data.neuro.polymtl.ca/datasets/spine-ms",
                "sub-01",
                "spine-ms",
            ),
            (
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                "sub-amuAP",
                "whole-spine",
            ),
            (
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                "sub-amuLJ",
                "whole-spine",
            ),
        ]

        with patch.object(dnp, "clone_sparse"):
            dnp.download_subjects(subjects, tmp_path, use_annex=False)

        assert obs.on_repo_done.call_count == 2
        called_repos = {c.args[0] for c in obs.on_repo_done.call_args_list}
        assert called_repos == {"spine-ms", "whole-spine"}

    def test_no_observer_required_backward_compatible(self, tmp_path):
        dnp = self._make_manager()
        subjects = [
            (
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                "sub-amuAP",
                "whole-spine",
            )
        ]

        with patch.object(dnp, "clone_sparse"), patch.object(dnp, "annex_get"):
            results = dnp.download_subjects(subjects, tmp_path, use_annex=True)

        assert len(results) == 1
        assert results[0][0] is True
