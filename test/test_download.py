"""
Offline tests for the ``npdb download`` command and its supporting classes.

All network I/O (git subprocesses, httpx, gitea client) is mocked so that
these tests run without any server access.
"""

import io
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from npdb.cli.cli import npdb
from npdb.cli.helpers import fetch_url, read_tsv
from npdb.providers.neuropoly import DataNeuroPolyMTL

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TSV_HEADER = "\t".join(
    [
        "DatasetName",
        "RepositoryURL",
        "NumMatchingSubjects",
        "SubjectID",
        "SessionID",
        "ImagingSessionPath",
        "SessionType",
        "NumMatchingPhenotypicSessions",
        "NumMatchingImagingSessions",
        "Age",
        "Sex",
        "Diagnosis",
        "Assessment",
        "SessionImagingModalities",
        "SessionCompletedPipelines",
        "DatasetImagingModalities",
        "DatasetPipelines",
        "AccessLink",
    ]
)


def _make_row(**kwargs) -> str:
    """Return a TSV data row with sensible defaults, overridable via kwargs."""
    defaults = {
        "DatasetName": "whole-spine",
        "RepositoryURL": "https://data.neuro.polymtl.ca/datasets/whole-spine",
        "NumMatchingSubjects": "1",
        "SubjectID": "sub-amuAP",
        "SessionID": "ses-01",
        "ImagingSessionPath": "sub-amuAP",
        "SessionType": "ImagingSession",
        "NumMatchingPhenotypicSessions": "1",
        "NumMatchingImagingSessions": "1",
        "Age": "30.0",
        "Sex": "M",
        "Diagnosis": "",
        "Assessment": "",
        "SessionImagingModalities": "T1w",
        "SessionCompletedPipelines": "",
        "DatasetImagingModalities": "T1w",
        "DatasetPipelines": "",
        "AccessLink": "https://intranet.neuro.polymtl.ca/data/README.html",
    }
    defaults.update(kwargs)
    return "\t".join(defaults[k] for k in TSV_HEADER.split("\t"))


def _write_tsv(tmp_path: Path, rows: list[str]) -> Path:
    tsv = tmp_path / "results.tsv"
    tsv.write_text(TSV_HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return tsv


# ---------------------------------------------------------------------------
# GiteaProvider — URL parsing and protocol helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def provider(tmp_path):
    """A DataNeuroPolyMTL instance with all network calls mocked (no server needed)."""
    with (
        patch("npdb.external.neurodata.gitea.gt_client.Gitea") as MockGitea,
        patch("npdb.providers.neurodata.OrganizationMixin.__init__", return_value=None),
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


class TestGiteaProviderURLParsing:
    """GiteaProvider correctly parses URLs with or without a scheme."""

    @pytest.mark.parametrize(
        "url,expected_host,expected_proto",
        [
            ("https://data.neuro.polymtl.ca", "data.neuro.polymtl.ca", "https"),
            ("http://data.neuro.polymtl.ca", "data.neuro.polymtl.ca", "http"),
            ("data.neuro.polymtl.ca", "data.neuro.polymtl.ca", "https"),
            ("data.neuro.polymtl.ca/extra/path", "data.neuro.polymtl.ca", "https"),
            ("https://data.neuro.polymtl.ca/", "data.neuro.polymtl.ca", "https"),
        ],
    )
    def test_host_and_proto_extracted(self, url, expected_host, expected_proto):
        with (
            patch("npdb.external.neurodata.gitea.gt_client.Gitea") as MockGitea,
            patch(
                "npdb.providers.neurodata.OrganizationMixin.__init__", return_value=None
            ),
        ):
            MockGitea.return_value = MagicMock()
            mgr = DataNeuroPolyMTL(url=url, user="u", token="t")
        assert mgr.host == expected_host
        assert mgr._proto == expected_proto

    def test_http_base_constructed(self):
        with (
            patch("npdb.external.neurodata.gitea.gt_client.Gitea") as MockGitea,
            patch(
                "npdb.providers.neurodata.OrganizationMixin.__init__", return_value=None
            ),
        ):
            MockGitea.return_value = MagicMock()
            mgr = DataNeuroPolyMTL(
                url="https://data.neuro.polymtl.ca", user="u", token="t"
            )
        assert mgr._http_base == "https://data.neuro.polymtl.ca"

    def test_gitea_client_receives_normalised_url(self):
        with (
            patch("npdb.external.neurodata.gitea.gt_client.Gitea") as MockGitea,
            patch(
                "npdb.providers.neurodata.OrganizationMixin.__init__", return_value=None
            ),
        ):
            MockGitea.return_value = MagicMock()
            DataNeuroPolyMTL(url="data.neuro.polymtl.ca", user="u", token="t")
        MockGitea.assert_called_once()
        args, kwargs = MockGitea.call_args
        assert kwargs.get("gitea_url") == "https://data.neuro.polymtl.ca"


class TestToSshUrl:
    """_to_ssh_url converts all supported HTTP URL forms correctly."""

    @pytest.mark.parametrize(
        "http_url,expected",
        [
            (
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                "git@data.neuro.polymtl.ca:datasets/whole-spine.git",
            ),
            (
                "https://data.neuro.polymtl.ca/datasets/whole-spine.git",
                "git@data.neuro.polymtl.ca:datasets/whole-spine.git",
            ),
            (
                "http://data.neuro.polymtl.ca/datasets/whole-spine",
                "git@data.neuro.polymtl.ca:datasets/whole-spine.git",
            ),
            (
                # bare host (no scheme) — should still work
                "data.neuro.polymtl.ca/datasets/whole-spine",
                "git@data.neuro.polymtl.ca:datasets/whole-spine.git",
            ),
            (
                # trailing slash stripped
                "https://data.neuro.polymtl.ca/datasets/whole-spine/",
                "git@data.neuro.polymtl.ca:datasets/whole-spine.git",
            ),
            # --- SSH inputs (already-converted; must be idempotent) ---
            # annex_get calls _to_ssh_url with the value returned by
            # "git remote get-url origin".  On a repo that was initialised
            # in a previous run the origin is already in SSH form, so
            # _to_ssh_url receives an SSH URL, not an HTTPS one.
            (
                # correct SSH URL — must pass through unchanged
                "git@data.neuro.polymtl.ca:datasets/whole-spine.git",
                "git@data.neuro.polymtl.ca:datasets/whole-spine.git",
            ),
            (
                # SSH URL without .git suffix — must be normalised
                "git@data.neuro.polymtl.ca:datasets/whole-spine",
                "git@data.neuro.polymtl.ca:datasets/whole-spine.git",
            ),
        ],
    )
    def test_conversion(self, provider, http_url, expected):
        assert provider._to_ssh_url(http_url) == expected


class TestGitHttpConfig:
    """git_http_config returns properly formatted -c flags."""

    def test_contains_authorization_header(self, provider):
        config = provider.git_http_config()
        joined = " ".join(config)
        assert "http.extraHeader=Authorization: Basic" in joined

    def test_flag_pairs(self, provider):
        config = provider.git_http_config()
        # Must come in ["-c", "key=val"] pairs
        assert len(config) % 2 == 0
        for i in range(0, len(config), 2):
            assert config[i] == "-c"
            assert "=" in config[i + 1]

    def test_ssl_verify_false(self, provider):
        config = provider.git_http_config()
        joined = " ".join(config)
        assert "http.sslVerify=false" in joined


class TestGitEnv:
    """git_env must disable interactive credential helpers."""

    def test_overrides_vscode_askpass(self, provider, monkeypatch):
        monkeypatch.setenv("GIT_ASKPASS", "/tmp/vscode-askpass.sh")
        monkeypatch.setenv("VSCODE_GIT_ASKPASS_NODE", "node")
        monkeypatch.setenv("VSCODE_GIT_ASKPASS_MAIN", "main.js")
        monkeypatch.setenv("VSCODE_GIT_ASKPASS_EXTRA_ARGS", "")

        env = provider.git_env()

        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GIT_ASKPASS"] == "/bin/echo"
        assert env["SSH_ASKPASS"] == "/bin/echo"
        assert env["GCM_INTERACTIVE"] == "never"
        assert "VSCODE_GIT_ASKPASS_NODE" not in env
        assert "VSCODE_GIT_ASKPASS_MAIN" not in env
        assert "VSCODE_GIT_ASKPASS_EXTRA_ARGS" not in env


# ---------------------------------------------------------------------------
# GiteaProvider — clone_sparse
# ---------------------------------------------------------------------------


class TestCloneSparse:
    """clone_sparse issues the right sequence of git subprocesses."""

    def test_raises_on_empty_paths(self, provider, tmp_path):
        with pytest.raises(
            ValueError, match="sparse_paths must contain at least one path"
        ):
            provider.clone_sparse("https://example.com/repo", [], tmp_path / "dest")

    def test_clone_then_sparse_checkout(self, provider, tmp_path):
        dest = tmp_path / "repo"

        with patch.object(provider, "_run_git") as mock_run_git:
            provider.clone_sparse(
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                ["sub-amuAP", "sub-amuLJ"],
                dest,
            )

        # _run_git(cmd, env=env, context=...) → args[0]=cmd, kwargs['context']=...
        # Context strings: "clone '...'" | "sparse-checkout init in '...'" |
        #                  "sparse-checkout set [...] in '...'" | "checkout in '...'"
        contexts = [c.kwargs["context"] for c in mock_run_git.call_args_list]
        cmds = [" ".join(c.args[0]) for c in mock_run_git.call_args_list]

        assert any(
            ctx.startswith("clone ") for ctx in contexts
        ), f"Expected a clone call, contexts: {contexts}"
        assert any(
            "whole-spine.git" in cmd for cmd in cmds
        ), f"Clone URL not found, cmds: {cmds}"
        assert any(
            ctx.startswith("sparse-checkout") and "init" in ctx for ctx in contexts
        )
        assert any(
            ctx.startswith("sparse-checkout") and "set" in ctx for ctx in contexts
        )
        assert any(ctx.startswith("checkout") for ctx in contexts)

    def test_skips_clone_when_git_dir_exists(self, provider, tmp_path):
        dest = tmp_path / "repo"
        dest.mkdir()
        (dest / ".git").mkdir()  # simulate existing clone

        with patch.object(provider, "_run_git") as mock_run_git:
            provider.clone_sparse(
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                ["sub-amuAP"],
                dest,
            )

        contexts = [c.kwargs["context"] for c in mock_run_git.call_args_list]
        assert not any(
            ctx.startswith("clone ") for ctx in contexts
        ), f"Should not clone when .git already exists, contexts: {contexts}"

    def test_url_normalised_to_https(self, provider, tmp_path):
        """repo_url with bare host or http is upgraded to the configured https base."""
        dest = tmp_path / "repo"

        with patch.object(provider, "_run_git") as mock_run_git:
            provider.clone_sparse(
                "data.neuro.polymtl.ca/datasets/whole-spine",
                ["sub-amuAP"],
                dest,
            )

        clone_cmds = [
            " ".join(c.args[0])
            for c in mock_run_git.call_args_list
            if c.kwargs["context"].startswith("clone ")
        ]
        assert clone_cmds, "Expected a clone call"
        assert "https://data.neuro.polymtl.ca/datasets/whole-spine.git" in clone_cmds[0]

    def test_subprocess_failure_raises_runtime_error(self, provider, tmp_path):
        dest = tmp_path / "repo"
        popen_proc = MagicMock()
        popen_proc.stdout = io.StringIO("")
        popen_proc.stderr = io.StringIO("fatal: repo not found\n")
        popen_proc.wait.return_value = 128
        with patch("subprocess.Popen", return_value=popen_proc):
            with pytest.raises(RuntimeError, match="failed"):
                provider.clone_sparse(
                    "https://data.neuro.polymtl.ca/datasets/ds",
                    ["sub-01"],
                    dest,
                )


# ---------------------------------------------------------------------------
# GiteaProvider — annex_get
# ---------------------------------------------------------------------------


class TestAnnexGet:
    """annex_get issues the right sequence of git/git-annex subprocesses."""

    def _run_annex_get(self, provider, tmp_path, paths=None):
        """Helper: run annex_get with all subprocess calls mocked."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        https_url = "https://data.neuro.polymtl.ca/datasets/whole-spine.git"

        def fake_popen(cmd, **kwargs):
            proc = MagicMock()
            if "get-url" in cmd:
                proc.stdout = io.StringIO(https_url + "\n")
            else:
                proc.stdout = io.StringIO("")
            proc.stderr = io.StringIO("")
            proc.wait.return_value = 0
            return proc

        with patch("subprocess.Popen", side_effect=fake_popen):
            provider.annex_get(repo_dir, paths)

        return repo_dir

    def _capture_commands(self, provider, tmp_path, paths=None):
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        https_url = "https://data.neuro.polymtl.ca/datasets/whole-spine.git"
        captured = []

        def fake_popen(cmd, **kwargs):
            captured.append(list(cmd))
            proc = MagicMock()
            if "get-url" in cmd:
                proc.stdout = io.StringIO(https_url + "\n")
            else:
                proc.stdout = io.StringIO("")
            proc.stderr = io.StringIO("")
            proc.wait.return_value = 0
            return proc

        with patch("subprocess.Popen", side_effect=fake_popen):
            provider.annex_get(repo_dir, paths)

        return captured

    def test_switches_origin_to_ssh(self, provider, tmp_path):
        cmds = self._capture_commands(provider, tmp_path, ["sub-amuAP"])
        set_url_cmds = [c for c in cmds if "set-url" in c]
        assert set_url_cmds, "Expected a 'remote set-url' command"
        ssh_url = set_url_cmds[0][-1]
        assert ssh_url.startswith("git@"), f"Expected SSH URL, got: {ssh_url}"
        assert "datasets/whole-spine.git" in ssh_url

    def test_fetches_git_annex_branch(self, provider, tmp_path):
        cmds = self._capture_commands(provider, tmp_path, ["sub-amuAP"])
        fetch_cmds = [c for c in cmds if "fetch" in c]
        assert fetch_cmds, "Expected a git fetch command"
        fetch_args = " ".join(fetch_cmds[0])
        assert "git-annex" in fetch_args
        assert "refs/remotes/origin/git-annex" in fetch_args

    def test_runs_annex_init(self, provider, tmp_path):
        cmds = self._capture_commands(provider, tmp_path, ["sub-amuAP"])
        assert any("annex" in c and "init" in c for c in cmds)

    def test_unsets_annex_ignore(self, provider, tmp_path):
        cmds = self._capture_commands(provider, tmp_path, ["sub-amuAP"])
        config_cmds = [
            c for c in cmds if "config" in c and "annex-ignore" in " ".join(c)
        ]
        assert config_cmds, "Expected a git config annex-ignore command"
        assert "false" in config_cmds[0]

    def test_runs_annex_merge(self, provider, tmp_path):
        cmds = self._capture_commands(provider, tmp_path, ["sub-amuAP"])
        assert any("annex" in c and "merge" in c for c in cmds)

    def test_runs_annex_get_with_paths(self, provider, tmp_path):
        paths = ["sub-amuAP", "sub-amuLJ"]
        cmds = self._capture_commands(provider, tmp_path, paths)
        get_cmds = [c for c in cmds if "annex" in c and "get" in c]
        assert get_cmds, "Expected a git annex get command"
        get_flat = " ".join(get_cmds[0])
        assert "sub-amuAP" in get_flat
        assert "sub-amuLJ" in get_flat

    def test_default_path_is_dot(self, provider, tmp_path):
        cmds = self._capture_commands(provider, tmp_path, None)
        get_cmds = [c for c in cmds if "annex" in c and "get" in c]
        assert get_cmds
        assert "." in get_cmds[0]

    def test_switches_origin_to_ssh_when_already_ssh(self, provider, tmp_path):
        """When origin is already SSH (repo reused from a previous run),
        annex_get must not mangle the URL further.

        This is the scenario observed in production: after a first successful
        (or partial) run, "git remote get-url origin" returns the SSH URL
        that was stored by the previous set-url call.  The second pass through
        _to_ssh_url must be idempotent — it must NOT drop the path component.
        """
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        # origin already switched to SSH by a prior run
        ssh_origin = "git@data.neuro.polymtl.ca:datasets/whole-spine.git"
        captured = []

        def fake_run(cmd, **kwargs):
            captured.append(list(cmd))
            proc = MagicMock()
            if "get-url" in cmd:
                proc.stdout = io.StringIO(ssh_origin + "\n")
            else:
                proc.stdout = io.StringIO("")
            proc.stderr = io.StringIO("")
            proc.wait.return_value = 0
            return proc

        with patch("subprocess.Popen", side_effect=fake_run):
            provider.annex_get(repo_dir, ["sub-amuAP"])

        set_url_cmds = [c for c in captured if "set-url" in c]
        assert set_url_cmds, "Expected a 'remote set-url' command"
        stored_url = set_url_cmds[0][-1]
        assert stored_url == ssh_origin, (
            f"annex_get mangled an already-SSH origin URL.\n"
            f"  input : {ssh_origin}\n"
            f"  stored: {stored_url}"
        )

    def test_command_order(self, provider, tmp_path):
        """fetch → SSH switch → init → config → merge → get (order is critical).

        The git-annex metadata branch fetch must happen over HTTPS (before the
        SSH switch) so that token auth covers it.  Only git-annex content
        transfer (annex get) needs SSH.
        """

        cmds = self._capture_commands(provider, tmp_path, ["sub-amuAP"])
        flat = [" ".join(c) for c in cmds]

        def idx(keyword):
            for i, c in enumerate(flat):
                if keyword in c:
                    return i
            return -1

        i_fetch = idx("fetch")
        i_seturl = idx("set-url")
        i_init = idx("annex init")
        i_config = idx("annex-ignore")
        i_merge = idx("annex merge")
        i_get = idx("annex get")

        assert i_fetch < i_seturl < i_init < i_config < i_merge < i_get, (
            f"Command order wrong: fetch={i_fetch} set-url={i_seturl} "
            f"init={i_init} config={i_config} merge={i_merge} get={i_get}\n"
            + "\n".join(f"  {i}: {c}" for i, c in enumerate(flat))
        )


# ---------------------------------------------------------------------------
# DataNeuroPolyMTL — download_subjects
# ---------------------------------------------------------------------------


@pytest.fixture()
def dnp(tmp_path):
    """DataNeuroPolyMTL instance with Gitea client and git ops fully mocked."""
    with patch("npdb.external.neurodata.gitea.gt_client.Gitea") as MockGitea:
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


class TestDownloadSubjects:
    """DataNeuroPolyMTL.download_subjects groups repos and delegates correctly."""

    def test_single_subject_single_repo(self, dnp, tmp_path):
        subjects = [
            (
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                "sub-amuAP",
                "whole-spine",
            ),
        ]
        with patch.object(dnp, "clone_sparse") as mock_clone:
            results = dnp.download_subjects(
                subjects, tmp_path, use_annex=False, derivatives=False
            )

        assert len(results) == 1
        ok, label, msg = results[0]
        assert ok
        mock_clone.assert_called_once_with(
            "https://data.neuro.polymtl.ca/datasets/whole-spine",
            ["sub-amuAP"],
            tmp_path / "whole-spine",
        )

    def test_multiple_subjects_same_repo_cloned_once(self, dnp, tmp_path):
        subjects = [
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
            (
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                "sub-amuPA",
                "whole-spine",
            ),
        ]
        with patch.object(dnp, "clone_sparse") as mock_clone:
            results = dnp.download_subjects(
                subjects, tmp_path, use_annex=False, derivatives=False
            )

        # Only one clone call despite three subjects
        assert mock_clone.call_count == 1
        _, call_paths, _ = mock_clone.call_args.args
        assert set(call_paths) == {"sub-amuAP", "sub-amuLJ", "sub-amuPA"}
        assert len(results) == 1

    def test_multiple_repos_cloned_independently(self, dnp, tmp_path):
        subjects = [
            ("https://data.neuro.polymtl.ca/datasets/spine-ms", "sub-01", "spine-ms"),
            (
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                "sub-amuAP",
                "whole-spine",
            ),
        ]
        with patch.object(dnp, "clone_sparse") as mock_clone:
            dnp.download_subjects(subjects, tmp_path, use_annex=False)

        assert mock_clone.call_count == 2
        repo_urls = {c.args[0] for c in mock_clone.call_args_list}
        assert "https://data.neuro.polymtl.ca/datasets/spine-ms" in repo_urls
        assert "https://data.neuro.polymtl.ca/datasets/whole-spine" in repo_urls

    def test_dest_is_output_dir_slash_dataset(self, dnp, tmp_path):
        subjects = [
            (
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                "sub-amuAP",
                "whole-spine",
            ),
        ]
        with patch.object(dnp, "clone_sparse") as mock_clone:
            dnp.download_subjects(subjects, tmp_path, use_annex=False)

        _, _, dest = mock_clone.call_args.args
        assert dest == tmp_path / "whole-spine"

    def test_annex_get_called_when_use_annex(self, dnp, tmp_path):
        subjects = [
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
        with (
            patch.object(dnp, "clone_sparse"),
            patch.object(dnp, "annex_get") as mock_annex,
        ):
            dnp.download_subjects(subjects, tmp_path, use_annex=True, derivatives=False)

        mock_annex.assert_called()
        assert mock_annex.call_count == 2
        repo_dir, paths = mock_annex.call_args_list[0].args
        assert repo_dir == tmp_path / "whole-spine"
        assert set(paths) == {"sub-amuAP", "sub-amuLJ"}
        (repo_dir,) = mock_annex.call_args_list[1].args
        assert repo_dir == tmp_path / "whole-spine"
        assert "includes" in mock_annex.call_args_list[1].kwargs
        assert mock_annex.call_args_list[1].kwargs["includes"] == []

    def test_annex_get_not_called_without_use_annex(self, dnp, tmp_path):
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
            dnp.download_subjects(subjects, tmp_path, use_annex=False)

        mock_annex.assert_not_called()

    def test_duplicate_sparse_paths_deduplicated(self, dnp, tmp_path):
        subjects = [
            (
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                "sub-amuAP",
                "whole-spine",
            ),
            (
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                "sub-amuAP",
                "whole-spine",
            ),
        ]
        with patch.object(dnp, "clone_sparse") as mock_clone:
            dnp.download_subjects(subjects, tmp_path, use_annex=False)

        _, paths, _ = mock_clone.call_args.args
        assert paths.count("sub-amuAP") == 1

    def test_clone_failure_reported_as_failed_result(self, dnp, tmp_path):
        subjects = [
            (
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                "sub-amuAP",
                "whole-spine",
            ),
        ]
        with patch.object(dnp, "clone_sparse", side_effect=RuntimeError("clone boom")):
            results = dnp.download_subjects(subjects, tmp_path, use_annex=False)

        assert len(results) == 1
        ok, _, msg = results[0]
        assert not ok
        assert "clone boom" in msg

    def test_annex_failure_reported_as_failed_result(self, dnp, tmp_path):
        subjects = [
            (
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                "sub-amuAP",
                "whole-spine",
            ),
        ]
        with (
            patch.object(dnp, "clone_sparse"),
            patch.object(dnp, "annex_get", side_effect=RuntimeError("annex boom")),
        ):
            results = dnp.download_subjects(subjects, tmp_path, use_annex=True)

        ok, _, msg = results[0]
        assert not ok
        assert "annex boom" in msg

    def test_empty_subjects_returns_empty_results(self, dnp, tmp_path):
        results = dnp.download_subjects([], tmp_path)
        assert results == []


# ---------------------------------------------------------------------------
# _read_download_tsv helper
# ---------------------------------------------------------------------------


class TestReadDownloadTsv:
    def test_parses_header_and_rows(self, tmp_path):
        tsv = _write_tsv(tmp_path, [_make_row(SubjectID="sub-01")])
        rows = read_tsv(tsv)
        assert len(rows) == 1
        assert rows[0]["SubjectID"] == "sub-01"

    def test_all_expected_columns_present(self, tmp_path):
        tsv = _write_tsv(tmp_path, [_make_row()])
        rows = read_tsv(tsv)
        for col in [
            "DatasetName",
            "RepositoryURL",
            "ImagingSessionPath",
            "SubjectID",
            "AccessLink",
        ]:
            assert col in rows[0]

    def test_raises_on_empty_file(self, tmp_path):
        tsv = tmp_path / "empty.tsv"
        tsv.write_text("", encoding="utf-8")
        with pytest.raises(ValueError, match="empty or has no header"):
            read_tsv(tsv)

    def test_raises_on_header_only(self, tmp_path):
        tsv = tmp_path / "header.tsv"
        tsv.write_text(TSV_HEADER + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match="no data rows"):
            read_tsv(tsv)

    def test_multiple_rows(self, tmp_path):
        rows_data = [_make_row(SubjectID=f"sub-0{i}") for i in range(5)]
        tsv = _write_tsv(tmp_path, rows_data)
        rows = read_tsv(tsv)
        assert len(rows) == 5


# ---------------------------------------------------------------------------
# _fetch_url helper
# ---------------------------------------------------------------------------


class TestFetchUrl:
    def test_successful_download(self, tmp_path):
        dest = tmp_path / "dataset" / "subject" / "file.nii.gz"
        fake_bytes = b"FAKE_NII_CONTENT"

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_bytes.return_value = iter([fake_bytes])
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("npdb.cli.helpers.httpx.stream", return_value=mock_response):
            ok, msg = fetch_url("https://example.com/file.nii.gz", dest)

        assert ok
        assert dest.exists()
        assert dest.read_bytes() == fake_bytes

    def test_creates_parent_directories(self, tmp_path):
        dest = tmp_path / "a" / "b" / "c" / "file.bin"

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_bytes.return_value = iter([b"data"])
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("npdb.cli.helpers.httpx.stream", return_value=mock_response):
            fetch_url("https://example.com/file.bin", dest)

        assert dest.parent.is_dir()

    def test_returns_false_on_http_error(self, tmp_path):
        dest = tmp_path / "file.bin"
        with patch(
            "npdb.cli.helpers.httpx.stream", side_effect=Exception("connection refused")
        ):
            ok, msg = fetch_url("https://example.com/file.bin", dest)

        assert not ok
        assert "connection refused" in msg


# ---------------------------------------------------------------------------
# CLI — download command (integration with mocked provider)
# ---------------------------------------------------------------------------

ENV_VARS = {
    "NP_GITEA_APP_URL": "https://data.neuro.polymtl.ca",
    "NP_GITEA_APP_USER": "testuser",
    "NP_GITEA_APP_TOKEN": "testtoken",
}


class TestDownloadCLI:
    """End-to-end CLI tests with subprocess and network calls mocked."""

    # ── help / validation ────────────────────────────────────────────────

    def test_download_help(self):
        result = runner.invoke(npdb, ["download", "--help"])
        assert result.exit_code == 0
        assert "download" in result.stdout.lower()

    def test_missing_env_vars_when_git_is_needed_errors(self, tmp_path):
        tsv = _write_tsv(tmp_path, [_make_row(AccessLink="")])
        with (
            patch("npdb.cli.cli.load_dotenv"),
            patch.dict("os.environ", {}, clear=True),
        ):
            result = runner.invoke(
                npdb,
                ["download", str(tsv), "--output-dir", str(tmp_path)],
            )
        assert result.exit_code != 0
        assert "NP_GITEA_APP_URL" in result.output

    # ── HTTP protocol ─────────────────────────────────────────────────────

    def test_http_protocol_dispatches_fetch(self, tmp_path):
        tsv = _write_tsv(
            tmp_path, [_make_row(AccessLink="https://example.com/data/file.nii.gz")]
        )

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_bytes.return_value = iter([b"data"])
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("npdb.cli.helpers.httpx.stream", return_value=mock_response):
            result = runner.invoke(
                npdb, ["download", str(tsv), "--output-dir", str(tmp_path)]
            )
        assert result.exit_code == 0
        assert "Download complete" in result.output

    def test_http_protocol_deduplicates_identical_links(self, tmp_path):
        row = _make_row(AccessLink="https://example.com/file.nii.gz")
        tsv = _write_tsv(tmp_path, [row, row])

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_bytes.return_value = iter([b"data"])
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch(
            "npdb.cli.helpers.httpx.stream", return_value=mock_response
        ) as mock_stream:
            runner.invoke(npdb, ["download", str(tsv), "--output-dir", str(tmp_path)])

        assert mock_stream.call_count == 1

    # ── git / git-annex auto-selection ───────────────────────────────────

    def test_git_protocol_calls_download_subjects_when_no_accesslink(self, tmp_path):
        tsv = _write_tsv(
            tmp_path,
            [
                _make_row(
                    SubjectID="sub-amuAP", ImagingSessionPath="sub-amuAP", AccessLink=""
                ),
                _make_row(
                    SubjectID="sub-amuLJ", ImagingSessionPath="sub-amuLJ", AccessLink=""
                ),
                # phenotypic row (no ImagingSessionPath) — must be filtered out
                _make_row(SubjectID="sub-amuAP", ImagingSessionPath="", AccessLink=""),
            ],
        )

        with (
            patch("npdb.cli.cli.load_dotenv"),
            patch.dict("os.environ", ENV_VARS),
            patch("npdb.cli.cli.repo_has_git_annex", return_value=False),
            patch("npdb.cli.cli.GiteaProviderFactory") as MockFactory,
        ):
            instance = MockFactory.create_from_env.return_value
            instance.download_subjects.return_value = [
                (True, "whole-spine [sub-amuAP, sub-amuLJ]", "OK"),
            ]
            result = runner.invoke(
                npdb,
                [
                    "download",
                    str(tsv),
                    "--output-dir",
                    str(tmp_path),
                    "--no-verify-ssl",
                ],
            )

        assert result.exit_code == 0, result.output
        instance.download_subjects.assert_called_once()
        call_subjects = instance.download_subjects.call_args.args[0]
        # Only the two ImagingSession rows should be passed
        assert len(call_subjects) == 2
        assert all(path != "" for _, path, _ in call_subjects)

    def test_dataset_with_accesslink_skips_git(self, tmp_path):
        tsv = _write_tsv(
            tmp_path,
            [
                _make_row(
                    DatasetName="dataset-http",
                    RepositoryURL="https://data.neuro.polymtl.ca/datasets/dataset-http",
                    AccessLink="https://example.com/http-only/file.bin",
                    ImagingSessionPath="sub-amuAP",
                )
            ],
        )

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_bytes.return_value = iter([b"data"])
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with (
            patch("npdb.cli.helpers.httpx.stream", return_value=mock_response),
            patch("npdb.cli.cli.GiteaProviderFactory") as MockFactory,
        ):
            result = runner.invoke(
                npdb, ["download", str(tsv), "--output-dir", str(tmp_path)]
            )

        assert result.exit_code == 0, result.output
        MockFactory.create_from_env.assert_not_called()

    def test_no_targets_warns(self, tmp_path):
        # All rows have empty ImagingSessionPath
        tsv = _write_tsv(tmp_path, [_make_row(ImagingSessionPath="", AccessLink="")])
        with (
            patch("npdb.cli.cli.load_dotenv"),
            patch.dict("os.environ", ENV_VARS),
            patch("npdb.cli.cli.GiteaProviderFactory"),
        ):
            result = runner.invoke(
                npdb,
                ["download", str(tsv), "--output-dir", str(tmp_path)],
            )
        assert "No download targets found" in result.output

    def test_git_protocol_failure_exits_nonzero(self, tmp_path):
        tsv = _write_tsv(
            tmp_path, [_make_row(ImagingSessionPath="sub-amuAP", AccessLink="")]
        )
        with (
            patch("npdb.cli.cli.load_dotenv"),
            patch.dict("os.environ", ENV_VARS),
            patch("npdb.cli.cli.repo_has_git_annex", return_value=False),
            patch("npdb.cli.cli.GiteaProviderFactory") as MockFactory,
        ):
            instance = MockFactory.create_from_env.return_value
            instance.download_subjects.return_value = [
                (False, "whole-spine [sub-amuAP]", "fatal: some git error"),
            ]
            result = runner.invoke(
                npdb,
                ["download", str(tsv), "--output-dir", str(tmp_path)],
            )
        assert result.exit_code != 0
        assert "failures" in result.output.lower()

    def test_annex_detected_passes_use_annex_true(self, tmp_path):
        tsv = _write_tsv(
            tmp_path, [_make_row(ImagingSessionPath="sub-amuAP", AccessLink="")]
        )
        with (
            patch("npdb.cli.cli.load_dotenv"),
            patch.dict("os.environ", ENV_VARS),
            patch("npdb.cli.cli.repo_has_git_annex", return_value=True),
            patch("npdb.cli.cli.GiteaProviderFactory") as MockFactory,
        ):
            instance = MockFactory.create_from_env.return_value
            instance.download_subjects.return_value = [
                (True, "whole-spine [sub-amuAP]", "OK")
            ]
            runner.invoke(
                npdb,
                [
                    "download",
                    str(tsv),
                    "--output-dir",
                    str(tmp_path),
                    "--no-verify-ssl",
                ],
            )

        _, call_kwargs = instance.download_subjects.call_args
        # use_annex is the third positional arg or keyword
        call_args = instance.download_subjects.call_args
        use_annex_val = (
            call_kwargs.get("use_annex")
            if call_kwargs.get("use_annex") is not None
            else call_args.args[2] if len(call_args.args) > 2 else None
        )
        assert use_annex_val is True
