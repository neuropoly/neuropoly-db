"""Unified CLI error handling: every anticipated failure gets a tailored message."""

import errno
import json
import socket
import ssl
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from typer.testing import CliRunner

from npdb.annotation.preflight import PreflightError
from npdb.bids_repair import BidsRepairError
from npdb.cli.cli import npdb
from npdb.cli.errors import ISSUE_URL_LIMIT, diagnose, issue_url
from npdb.errors import ConfigurationError, GitCommandError, UsageError, redact
from npdb.external.bids import BidsValidationError
from npdb.external.neurobagel.errors import BagelCLIError
from npdb.extras import MissingExecutableError, MissingExtraError

runner = CliRunner()


def http_error(status):
    request = httpx.Request("GET", "https://provider.example/api")
    return httpx.HTTPStatusError(
        f"HTTP {status}",
        request=request,
        response=httpx.Response(status, request=request),
    )


class LibraryHTTPError(Exception):
    """Shape of requests/kagglehub HTTP errors: a response with a status code."""

    def __init__(self, status):
        super().__init__(f"{status} Client Error")
        self.response = type("R", (), {"status_code": status})()


def caused(outer, inner):
    try:
        raise outer from inner
    except BaseException as exc:
        return exc


def stop_iteration_in_coroutine():
    try:
        raise RuntimeError("coroutine raised StopIteration") from StopIteration()
    except RuntimeError as exc:
        return exc


def bug_during_http_error():
    try:
        try:
            raise http_error(404)
        except httpx.HTTPStatusError:
            raise AttributeError("'list' object has no attribute 'get'")
    except AttributeError as exc:
        return exc


CASES = [
    # (id, exception, title, exit code, hint fragment, is bug)
    ("usage", UsageError("bad"), "Invalid usage", 2, None, False),
    (
        "configuration",
        ConfigurationError("no env", hint="set it"),
        "Configuration error",
        1,
        "set it",
        False,
    ),
    (
        "missing-extra",
        MissingExtraError("Kaggle", "kaggle"),
        "Missing dependency",
        1,
        "npdb[kaggle]",
        False,
    ),
    (
        "missing-tool",
        MissingExecutableError("Git", "git", "Install git"),
        "Missing dependency",
        1,
        "Install git",
        False,
    ),
    (
        "git-auth",
        GitCommandError("clone failed.\nStderr: fatal: Authentication failed"),
        "Git error",
        1,
        "NP_GITEA_APP_TOKEN",
        False,
    ),
    (
        "git-ssl",
        GitCommandError("clone failed.\nStderr: SSL certificate problem: self-signed"),
        "Git error",
        1,
        "--no-verify-ssl",
        False,
    ),
    (
        "git-offline",
        GitCommandError("clone failed.\nStderr: Could not resolve host: x"),
        "Git error",
        1,
        "VPN",
        False,
    ),
    (
        "git-missing-repo",
        GitCommandError("clone failed.\nStderr: Repository not found."),
        "Git error",
        1,
        "dataset name",
        False,
    ),
    ("http-400", http_error(400), "Provider returned HTTP 400", 1, "identifier", False),
    (
        "http-401",
        http_error(401),
        "Provider returned HTTP 401",
        1,
        "credentials",
        False,
    ),
    (
        "http-403",
        http_error(403),
        "Provider returned HTTP 403",
        1,
        "credentials",
        False,
    ),
    ("http-404", http_error(404), "Provider returned HTTP 404", 1, "identifier", False),
    ("http-429", http_error(429), "Provider returned HTTP 429", 1, "retry", False),
    (
        "http-503",
        http_error(503),
        "Provider returned HTTP 503",
        1,
        "retry later",
        False,
    ),
    (
        "library-http",
        LibraryHTTPError(400),
        "Provider returned HTTP 400",
        1,
        "identifier",
        False,
    ),
    (
        "wrapped-http",
        caused(RuntimeError("fetch failed"), http_error(404)),
        "Provider returned HTTP 404",
        1,
        "identifier",
        False,
    ),
    ("connect", httpx.ConnectError("refused"), "Network error", 1, "VPN", False),
    ("connection", ConnectionError("reset"), "Network error", 1, "VPN", False),
    ("timeout", TimeoutError("slow"), "Network error", 1, "VPN", False),
    ("dns", socket.gaierror("no host"), "Network error", 1, "VPN", False),
    (
        "ssl",
        ssl.SSLCertVerificationError("verify failed"),
        "SSL certificate error",
        1,
        "--no-verify-ssl",
        False,
    ),
    (
        "ssl-text",
        Exception("[SSL: CERTIFICATE_VERIFY_FAILED] self-signed"),
        "SSL certificate error",
        1,
        "--no-verify-ssl",
        False,
    ),
    (
        "json",
        json.JSONDecodeError("Expecting value", "x", 0),
        "Invalid JSON",
        1,
        "JSON",
        False,
    ),
    (
        "disk-full",
        OSError(errno.ENOSPC, "No space left on device"),
        "Disk full",
        1,
        "--cache-dir",
        False,
    ),
    ("missing-file", FileNotFoundError("nope.tsv"), "File error", 1, "paths", False),
    ("exists", FileExistsError("dup"), "File error", 1, "paths", False),
    ("permission", PermissionError("denied"), "Permission denied", 1, "write", False),
    ("value", ValueError("No files were returned"), "Error", 1, None, False),
    (
        "bids-validation",
        BidsValidationError("3 errors", report={}),
        "BIDS validation failed",
        1,
        "repair bids",
        False,
    ),
    (
        "bids-repair",
        BidsRepairError("stale plan"),
        "BIDS repair failed",
        1,
        "repair recover",
        False,
    ),
    (
        "preflight",
        PreflightError(
            "Unsupported suffix",
            "TEM not supported",
            [{"action": "Add it", "detail": "to vocab"}],
        ),
        "Dataset check failed (Unsupported suffix)",
        1,
        "Add it: to vocab",
        False,
    ),
    (
        "bagel",
        BagelCLIError.from_result("pheno", 1, "Found duplicate participant IDs"),
        "Neurobagel conversion failed",
        1,
        "repeated",
        False,
    ),
    ("interrupt", KeyboardInterrupt(), "Interrupted", 130, "repair recover", False),
    (
        "bug-stopiteration",
        stop_iteration_in_coroutine(),
        "Unexpected error",
        1,
        None,
        True,
    ),
    (
        "bug-attribute",
        AttributeError("'list' object has no attribute 'get'"),
        "Unexpected error",
        1,
        None,
        True,
    ),
    ("bug-key", KeyError("files"), "Unexpected error", 1, None, True),
    ("bug-type", TypeError("unsupported operand"), "Unexpected error", 1, None, True),
    (
        "bug-not-masked-by-context",
        bug_during_http_error(),
        "Unexpected error",
        1,
        None,
        True,
    ),
]


@pytest.mark.parametrize(
    ("exc", "title", "code", "hint", "bug"),
    [c[1:] for c in CASES],
    ids=[c[0] for c in CASES],
)
def test_diagnosis(exc, title, code, hint, bug):
    diagnosis = diagnose(exc)
    assert diagnosis.title == title
    assert diagnosis.exit_code == code
    assert diagnosis.bug is bug
    if hint:
        assert hint in (diagnosis.hint or "")


@pytest.mark.parametrize(
    "command",
    [
        "git",
        "kaggle",
        "mendeley",
        "midrc",
        "openneuro",
        "zenodo",
        "figshare",
        "gitea",
        "download",
    ],
)
def test_remote_errors_link_the_provider_page(command):
    for exc in (http_error(404), httpx.ConnectError("x"), GitCommandError("failed")):
        assert diagnose(exc, command).docs.startswith("Development-Provider-")


def test_redact_removes_credentials_and_home(monkeypatch):
    monkeypatch.setenv("NP_GITEA_APP_TOKEN", "supersecret123")
    text = (
        f"git -c 'http.extraHeader=Authorization: Basic dXNlcjp0b2s=' clone "
        f"https://u:p@host/x?token=abc {Path.home()}/data supersecret123"
    )
    clean = redact(text)
    for secret in (
        "dXNlcjp0b2s=",
        "u:p@",
        "token=abc",
        "supersecret123",
        str(Path.home()),
    ):
        assert secret not in clean
    assert "~/data" in clean


def test_issue_url_respects_length_limit():
    url = issue_url("t", "x" * 100)
    query = parse_qs(urlparse(url).query)
    assert query["title"] == ["t"]
    assert url.startswith("https://github.com/neuropoly/neuropoly-db/issues/new?")


# --- every command, through the CLI ---------------------------------------------------


def local_args(tmp_path, *extra):
    dataset = tmp_path / "ds"
    dataset.mkdir(exist_ok=True)
    return [
        "convert",
        "bagel",
        "local",
        str(dataset),
        "https://example.org",
        str(tmp_path / "out"),
        *extra,
    ]


def assert_clean(result, code, *fragments):
    assert result.exit_code == code, result.output
    assert "Traceback (most recent call last)" not in result.output
    assert "╭" not in result.output
    for fragment in fragments:
        assert fragment in result.output, result.output


@pytest.mark.parametrize(
    ("extra", "fragment"),
    [
        (["--mode", "nope"], "Invalid mode 'nope'"),
        (["--ai-provider", "ollama"], "--ai-model required"),
        (["--ai-model", "m"], "--ai-provider required"),
        (["--suffix-alias", "bad"], "Invalid suffix alias"),
    ],
)
def test_convert_usage_errors(tmp_path, extra, fragment):
    assert_clean(
        runner.invoke(npdb, local_args(tmp_path, *extra)), 2, "Invalid usage", fragment
    )


@pytest.mark.parametrize(
    ("exc", "fragments"),
    [
        (
            BidsValidationError("2 errors above warning", report={}),
            ["BIDS validation failed", "repair bids"],
        ),
        (
            PreflightError("Unsupported suffix", "TEM not supported"),
            ["Dataset check failed"],
        ),
        (
            BagelCLIError.from_result("pheno", 1, "duplicate participant IDs"),
            ["Neurobagel conversion failed"],
        ),
        (
            FileNotFoundError("participants.tsv not found"),
            ["File error", "participants.tsv"],
        ),
    ],
)
def test_convert_dataset_errors(tmp_path, exc, fragments):
    with patch(
        "npdb.cli.facade.DatasetConversionFacade.run", new=AsyncMock(side_effect=exc)
    ):
        result = runner.invoke(npdb, local_args(tmp_path))
    assert_clean(result, 1, *fragments, "NPDB_DEBUG=1")


def test_convert_fails_when_annotation_does_not_complete(tmp_path):
    dataset = tmp_path / "ds"
    dataset.mkdir()
    (dataset / "dataset_description.json").write_text('{"Name": "x"}')
    (dataset / "participants.tsv").write_text("participant_id\nsub-01\n")
    with (
        patch("npdb.cli.facade.validate_and_repair_bids_dataset", return_value={}),
        patch(
            "npdb.cli.facade.NeurobagelAnnotator.execute",
            new=AsyncMock(return_value=False),
        ),
    ):
        result = runner.invoke(npdb, local_args(tmp_path))
    assert_clean(result, 1, "Annotation did not complete")
    assert "Conversion complete" not in result.output


def test_bug_writes_report_and_issue_link(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NP_GITEA_APP_TOKEN", "supersecret123")
    exc = AttributeError("'list' object has no attribute 'get' supersecret123")
    with patch(
        "npdb.cli.facade.DatasetConversionFacade.run", new=AsyncMock(side_effect=exc)
    ):
        result = runner.invoke(npdb, local_args(tmp_path))
    assert_clean(result, 1, "Unexpected error", "bug in npdb", "issues/new?")
    reports = list(tmp_path.glob("npdb-error-*.md"))
    assert len(reports) == 1
    report = reports[0].read_text()
    for section in (
        "### Command",
        "### Error",
        "### Environment",
        "<details>",
        "AttributeError",
    ):
        assert section in report
    assert "supersecret123" not in report + result.output
    link = next(
        line.split(": ", 1)[1]
        for line in result.output.splitlines()
        if "issues/new?" in line
    )
    assert len(link) <= ISSUE_URL_LIMIT
    assert "AttributeError" in parse_qs(urlparse(link).query)["body"][0]


def test_report_falls_back_to_temp_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path / "missing")
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    with patch(
        "npdb.cli.facade.DatasetConversionFacade.run",
        new=AsyncMock(side_effect=KeyError("x")),
    ):
        result = runner.invoke(npdb, local_args(tmp_path))
    assert result.exit_code == 1
    assert list(tmp_path.glob("npdb-error-*.md"))


def test_debug_mode_reports_expected_errors_too(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NPDB_DEBUG", "1")
    with patch(
        "npdb.cli.facade.DatasetConversionFacade.run",
        new=AsyncMock(side_effect=ValueError("bad")),
    ):
        result = runner.invoke(npdb, local_args(tmp_path))
    assert result.exit_code == 1
    assert "Traceback" in result.output
    assert list(tmp_path.glob("npdb-error-*.md"))


def test_standardize_errors(tmp_path):
    dataset = tmp_path / "ds"
    dataset.mkdir()
    assert_clean(
        runner.invoke(npdb, ["standardize", "bids", str(dataset)]),
        1,
        "participants.tsv not found",
    )
    assert_clean(
        runner.invoke(npdb, ["standardize", "bids", str(dataset), "--mode", "assist"]),
        2,
        "no longer supported",
    )
    (dataset / "participants.tsv").write_text("participant_id\nsub-01\n")
    with patch(
        "npdb.cli.facade.BIDSStandardizationFacade.run",
        new=AsyncMock(side_effect=stop_iteration_in_coroutine()),
    ):
        result = runner.invoke(
            npdb, ["standardize", "bids", str(dataset)], catch_exceptions=False
        )
    assert_clean(
        result, 1, "Unexpected error", "RuntimeError: coroutine raised StopIteration"
    )


def test_standardize_fails_when_standardizer_fails(tmp_path):
    dataset = tmp_path / "ds"
    dataset.mkdir()
    (dataset / "participants.tsv").write_text("participant_id\nsub-01\n")
    with patch(
        "npdb.cli.facade.BIDSStandardizer.execute", new=AsyncMock(return_value=False)
    ):
        result = runner.invoke(npdb, ["standardize", "bids", str(dataset)])
    assert_clean(result, 1, "Standardization did not complete")


@pytest.mark.parametrize("fmt", ["human", "json"])
def test_repair_errors(tmp_path, fmt):
    with patch(
        "npdb.bids_repair.plan_bids_repair", side_effect=BidsRepairError("stale plan")
    ):
        result = runner.invoke(npdb, ["repair", "bids", str(tmp_path), "--format", fmt])
    assert result.exit_code == 1
    if fmt == "json":
        payload = json.loads(result.output.strip().splitlines()[-1])
        assert payload["error"]["title"] == "BIDS repair failed"
        assert "repair recover" in payload["error"]["hint"]
    else:
        assert_clean(result, 1, "BIDS repair failed")
    with patch(
        "npdb.bids_repair.recover_bids_repair",
        side_effect=BidsRepairError("journal corrupt"),
    ):
        assert_clean(
            runner.invoke(npdb, ["repair", "recover", str(tmp_path)]),
            1,
            "journal corrupt",
        )
    assert_clean(
        runner.invoke(npdb, ["repair", "bids", str(tmp_path), "--format", "xml"]),
        2,
        "--format",
    )
    assert_clean(
        runner.invoke(npdb, ["repair", "bids", str(tmp_path), "--apply", "--dry-run"]),
        2,
        "cannot be used together",
    )


def gitea_tsv(tmp_path):
    tsv = tmp_path / "q.tsv"
    tsv.write_text(
        "DatasetName\tSubjectID\tRepositoryURL\tImagingSessionPath\tAccessLink\n"
        "ds\tsub-01\thttps://example.invalid/datasets/ds\tsub-01\t\n"
    )
    return tsv


@pytest.fixture
def no_gitea_env(monkeypatch):
    for name in ("NP_GITEA_APP_URL", "NP_GITEA_APP_USER", "NP_GITEA_APP_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("npdb.cli.cli._load_env", lambda: None)


def test_download_and_gitea_missing_credentials(tmp_path, no_gitea_env):
    result = runner.invoke(
        npdb, ["download", str(gitea_tsv(tmp_path)), "-o", str(tmp_path / "dl")]
    )
    assert_clean(
        result,
        1,
        "Configuration error",
        "NP_GITEA_APP_URL",
        "Development-Provider-NeuroData",
    )
    result = runner.invoke(
        npdb, ["convert", "bagel", "gitea", "ds", str(tmp_path / "out")]
    )
    assert_clean(result, 1, "Configuration error", "NP_GITEA_APP_TOKEN")


def test_gitea_clone_failure_is_explained(tmp_path):
    manager = type("M", (), {})()
    manager.clone_repository = lambda *a, **k: (_ for _ in ()).throw(
        GitCommandError("clone failed.\nStderr: fatal: Authentication failed")
    )
    with (
        patch("npdb.cli.cli._load_env"),
        patch("npdb.cli.cli.GiteaManagerFactory.create_from_env", return_value=manager),
    ):
        result = runner.invoke(
            npdb, ["convert", "bagel", "gitea", "ds", str(tmp_path / "out")]
        )
    assert_clean(
        result, 1, "Git error", "NP_GITEA_APP_TOKEN", "Development-Provider-NeuroData"
    )


def test_download_interrupt(tmp_path):
    manager = type(
        "M", (), {"verbose": False, "add_download_observer": lambda self, o: None}
    )()
    with (
        patch("npdb.cli.cli._load_env"),
        patch("npdb.cli.cli.GiteaManagerFactory.create_from_env", return_value=manager),
        patch("npdb.cli.cli.repo_has_git_annex", side_effect=KeyboardInterrupt),
    ):
        result = runner.invoke(
            npdb, ["download", str(gitea_tsv(tmp_path)), "-o", str(tmp_path / "dl")]
        )
    assert_clean(result, 130, "Interrupted")


def test_click_usage_errors_are_untouched():
    result = runner.invoke(npdb, ["convert", "bagel", "local"])
    assert result.exit_code == 2
    assert "Missing argument" in result.output
