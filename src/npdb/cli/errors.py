"""Single error-handling path for every npdb command.

Every command is wrapped by :func:`handle_errors` (via :class:`NpdbTyper`).
Exceptions are diagnosed by type into a :class:`Diagnosis`; anticipated ones are
printed as a short message with a hint and a docs link, anything else is treated
as a bug: a GitHub-ready report is written and a prefilled issue link is printed.
"""

from __future__ import annotations

import errno
import functools
import json
import os
import platform
import shlex
import socket
import ssl
import sys
import tempfile
import traceback
from dataclasses import dataclass
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import urlencode

import httpx
import typer

# Newer Typer releases have an independent Click context stack.
try:
    from typer._click.core import Context
    from typer._click.globals import get_current_context
except ImportError:
    from click import Context, get_current_context

from npdb.errors import ISSUES_URL, WIKI_URL, GitCommandError, NpdbError, redact

DEBUG_ENV = "NPDB_DEBUG"
ISSUE_URL_LIMIT = 7500

_PROVIDER_PAGES = {
    "download": "Development-Provider-NeuroData",
    "gitea": "Development-Provider-NeuroData",
    "git": "Development-Provider-Git",
    "kaggle": "Development-Provider-Kaggle",
    "mendeley": "Development-Provider-Mendeley",
    "midrc": "Development-Provider-MIDRC",
    "openneuro": "Development-Provider-OpenNeuro",
    "zenodo": "Development-Provider-Zenodo",
    "figshare": "Development-Provider-Figshare",
}
_EXTRA_MODULES = {
    "gitea": "gitea",
    "kaggle": "kagglehub",
    "midrc": "gen3",
    "openneuro": "openneuro",
    "zenodo": "zenodo_get",
    "annotation-automation": "playwright",
}
_TOOLS = ("git", "git-annex", "dcm2niix")

_GIT_HINTS = [
    (
        (
            "authentication failed",
            "could not read username",
            "http 401",
            "http 403",
            "permission denied (publickey)",
        ),
        "Check NP_GITEA_APP_USER / NP_GITEA_APP_TOKEN (or your SSH key) and that the token has Read access.",
        "Development-Provider-NeuroData#access",
    ),
    (
        (
            "ssl certificate problem",
            "certificate verify failed",
            "server certificate verification failed",
        ),
        "The server certificate is not trusted: rerun with --no-verify-ssl (safe behind the VPN).",
        "Development-Provider-NeuroData#details",
    ),
    (
        (
            "could not resolve host",
            "failed to connect",
            "connection timed out",
            "network is unreachable",
            "couldn't connect",
        ),
        "The server is unreachable: check your network and that the VPN is on.",
        None,
    ),
    (
        ("repository not found", "not found", "does not appear to be a git repository"),
        "The repository does not exist or you lack access: check the dataset name / RepositoryURL.",
        None,
    ),
]
_HTTP_HINTS = {
    400: "The provider rejected the request: check the identifier and options.",
    401: "Authentication failed: check the provider token or credentials.",
    403: "Access denied: check the provider token or credentials, and that you accepted the data use terms.",
    404: "Not found: check the dataset identifier (ID, DOI or URL).",
    429: "Rate limited by the provider: wait a few minutes and retry.",
}


@dataclass
class Diagnosis:
    title: str
    message: str
    hint: str | None = None
    docs: str | None = None
    exit_code: int = 1
    bug: bool = False


def _chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )


def _first_hint(text: str) -> tuple[str | None, str | None]:
    lowered = text.lower()
    for needles, hint, docs in _GIT_HINTS:
        if any(needle in lowered for needle in needles):
            return hint, docs
    return None, None


def _status_code(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    return code if isinstance(code, int) else None


def _diagnose_one(exc: BaseException) -> Diagnosis | None:
    from bids_repair import BidsRepairError, BidsValidationError
    from bids_repair.native import NativeValidatorError
    from npdb.annotation.preflight import PreflightError
    from npdb.external.neurobagel.errors import BagelCLIError, classify_bagel_error

    if isinstance(exc, KeyboardInterrupt):
        return Diagnosis(
            "Interrupted",
            "Stopped by the user.",
            hint="If a BIDS repair was being applied, restore the dataset with: bids-repair recover <dataset>",
            exit_code=130,
        )
    if isinstance(exc, GitCommandError):
        hint, docs = _first_hint(exc.message)
        return Diagnosis(
            exc.title, exc.message, exc.hint or hint, exc.docs or docs, exc.exit_code
        )
    if isinstance(exc, NpdbError):
        return Diagnosis(exc.title, exc.message, exc.hint, exc.docs, exc.exit_code)

    if isinstance(exc, NativeValidatorError):
        return Diagnosis(
            "Native BIDS validator unavailable",
            str(exc),
            hint="Reinstall a matching npdb wheel, or build the pinned validator for source development.",
            docs="Development-Environment-Setup#native-tools",
        )

    if isinstance(exc, BidsValidationError):
        return Diagnosis(
            "BIDS validation failed",
            str(exc),
            hint="Inspect the validation manifest above, try `bids-repair <dataset> --dry-run`, "
            "or allow more with --bids-severity-threshold.",
            docs="Development-BIDS-Repair",
        )
    if isinstance(exc, BidsRepairError):
        return Diagnosis(
            "BIDS repair failed",
            str(exc),
            hint="Restore the dataset with `bids-repair recover <dataset>`, then inspect the plan with --dry-run.",
            docs="Development-BIDS-Repair#manifests-journals-and-recovery",
        )

    if isinstance(exc, PreflightError):
        step = exc.fix_steps[0] if exc.fix_steps else None
        hint = f"{step['action']}: {step['detail']}" if step else None
        return Diagnosis(
            f"Dataset check failed ({exc.problem_name})",
            exc.description,
            hint,
            "Development-Ingestion#extending-imaging-modality-support",
        )
    if isinstance(exc, BagelCLIError):
        matches = classify_bagel_error(exc.plain_output)
        hint = None
        if matches:
            first = matches[0]
            steps = first.get("fix_steps") or []
            hint = first.get("description")
            if steps:
                hint = f"{hint} {steps[0]['action']}: {steps[0]['detail']}"
        tail = "\n".join(exc.plain_output.strip().splitlines()[-15:])
        return Diagnosis(
            "Neurobagel conversion failed",
            f"{exc}\n{tail}".strip(),
            hint,
            "Development-Ingestion#annotation-modes",
        )

    code = _status_code(exc)
    if code is not None:
        if code >= 500:
            hint = "The provider is having problems: retry later."
        else:
            hint = _HTTP_HINTS.get(code, "The provider rejected the request.")
        return Diagnosis(f"Provider returned HTTP {code}", str(exc), hint)

    text = str(exc)
    if isinstance(exc, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in text:
        return Diagnosis(
            "SSL certificate error",
            text,
            hint="For NeuroData, rerun with --no-verify-ssl (safe behind the VPN).",
            docs="Development-Provider-NeuroData#details",
        )
    if isinstance(
        exc, (httpx.TransportError, ConnectionError, TimeoutError, socket.gaierror)
    ):
        return Diagnosis(
            "Network error",
            text or type(exc).__name__,
            hint="Check your internet connection; for NeuroPoly services, turn on the VPN.",
        )

    if isinstance(exc, json.JSONDecodeError):
        return Diagnosis(
            "Invalid JSON",
            text,
            hint="Fix or regenerate the JSON file named in the message.",
        )
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return Diagnosis(
            "Disk full",
            text,
            hint="Free disk space or point --cache-dir / -o to a larger disk.",
        )
    if isinstance(
        exc, (FileNotFoundError, NotADirectoryError, IsADirectoryError, FileExistsError)
    ):
        return Diagnosis(
            "File error", text, hint="Check the paths given on the command line."
        )
    if isinstance(exc, PermissionError):
        return Diagnosis(
            "Permission denied",
            text,
            hint="Check you can read the inputs and write to the output folder.",
        )
    if isinstance(exc, ValueError):
        return Diagnosis("Error", text)
    return None


def diagnose(exc: BaseException, command: str | None = None) -> Diagnosis:
    """Map an exception (or its causes) to what the user sees. Unknown types are bugs."""
    for item in _chain(exc):
        diagnosis = _diagnose_one(item)
        if diagnosis is not None:
            remote = (
                isinstance(item, GitCommandError)
                or diagnosis.title
                in {
                    "Network error",
                    "SSL certificate error",
                }
                or diagnosis.title.startswith("Provider returned HTTP")
            )
            if diagnosis.docs is None and remote:
                diagnosis.docs = _PROVIDER_PAGES.get(command or "")
            return diagnosis
        if not _is_wrapper(item):
            break
    return Diagnosis("Unexpected error", f"{type(exc).__name__}: {exc}", bug=True)


def _is_wrapper(exc: BaseException) -> bool:
    """Generic wrappers whose cause carries the real reason (e.g. tenacity retries)."""
    from tenacity import RetryError

    return type(exc) in (Exception, RuntimeError) or isinstance(exc, RetryError)


def _docs_url(docs: str | None) -> str | None:
    if not docs:
        return None
    return docs if docs.startswith("http") else f"{WIKI_URL}/{docs}"


def _command_line(ctx: Context | None) -> str:
    if Path(sys.argv[0]).name.startswith("npdb"):
        return redact("npdb " + shlex.join(sys.argv[1:]))
    if ctx is None:
        return "npdb"
    params = " ".join(
        f"{k}={v!r}" for k, v in ctx.params.items() if v not in (None, False, [], ())
    )
    return redact(f"{ctx.command_path} {params}".strip())


def _environment() -> list[tuple[str, str]]:
    import importlib.util
    import shutil

    from bids_repair.native import validator_status

    try:
        dist = metadata.distribution("npdb")
        version = dist.version
        direct = dist.read_text("direct_url.json")
        commit = (
            json.loads(direct).get("vcs_info", {}).get("commit_id") if direct else None
        )
        if commit:
            version += f" (commit {commit[:10]})"
    except metadata.PackageNotFoundError:
        version = "not installed (source checkout)"

    extras = ", ".join(
        f"{extra} {'yes' if importlib.util.find_spec(module) else 'no'}"
        for extra, module in _EXTRA_MODULES.items()
    )
    tools = ", ".join(
        f"{tool} {'yes' if shutil.which(tool) else 'no'}" for tool in _TOOLS
    )
    tools += f", bids-validator {validator_status()}"
    return [
        ("npdb", version),
        ("Python", platform.python_version()),
        ("Platform", platform.platform()),
        ("Extras", extras),
        ("Tools", tools),
    ]


def build_report(
    exc: BaseException,
    diagnosis: Diagnosis,
    command: str,
    *,
    max_traceback: int | None = None,
) -> str:
    lines = traceback.format_exception(exc)
    trace = redact("".join(lines)).rstrip()
    if max_traceback is not None and len(trace) > max_traceback:
        trace = "...\n" + trace[-max_traceback:]

    env = "\n".join(f"| {key} | {value} |" for key, value in _environment())
    return (
        "### Command\n\n"
        f"```console\n$ {command}\n```\n\n"
        "### Error\n\n"
        f"**{diagnosis.title}**: `{redact(diagnosis.message.splitlines()[0] if diagnosis.message else '')}`\n\n"
        "### Environment\n\n"
        "| | |\n|-|-|\n"
        f"{env}\n\n"
        "<details><summary>Traceback</summary>\n\n"
        f"```text\n{trace}\n```\n\n"
        "</details>\n"
    )


def issue_url(title: str, body: str) -> str:
    return f"{ISSUES_URL}/new?{urlencode({'title': title, 'body': body})}"


def _issue_link(
    exc: BaseException, diagnosis: Diagnosis, command: str, report_path: Path | None
) -> str:
    title = redact(
        f"{command.split(' -')[0][:60]}: {diagnosis.message.splitlines()[0][:80]}"
    )
    body_limit = 4000
    while True:
        body = build_report(exc, diagnosis, command, max_traceback=body_limit)
        if report_path is not None:
            body += f"\n_Full report: `{report_path.name}` (attach it if the traceback is cut)._\n"
        url = issue_url(title, body)
        if len(url) <= ISSUE_URL_LIMIT or body_limit <= 200:
            return url
        body_limit //= 2


def write_report(text: str) -> Path:
    name = f"npdb-error-{datetime.now():%Y%m%d-%H%M%S}.md"
    for folder in (Path.cwd(), Path(tempfile.gettempdir())):
        try:
            path = folder / name
            path.write_text(text, encoding="utf-8")
            return path
        except OSError:
            continue
    raise OSError("Could not write the error report.")


def _json_mode(ctx: Context | None) -> bool:
    return bool(ctx and str(ctx.params.get("output_format", "")).lower() == "json")


def report_exception(exc: BaseException) -> int:
    """Render *exc* for the user; return the process exit code."""
    ctx = get_current_context(silent=True)
    command_name = ctx.info_name if ctx else None
    diagnosis = diagnose(exc, command_name)
    debug = os.environ.get(DEBUG_ENV, "").lower() in {"1", "true", "yes"}
    command = _command_line(ctx)

    report_path = link = None
    if diagnosis.bug or debug:
        report_path = write_report(build_report(exc, diagnosis, command))
        link = _issue_link(exc, diagnosis, command, report_path)

    docs = _docs_url(diagnosis.docs)
    if _json_mode(ctx):
        payload = {
            "title": diagnosis.title,
            "message": redact(diagnosis.message),
            "hint": diagnosis.hint,
            "docs": docs,
            "report": str(report_path) if report_path else None,
            "issue": link,
        }
        typer.echo(json.dumps({"error": payload}, sort_keys=True), err=True)
        return diagnosis.exit_code

    echo: Callable[..., None] = functools.partial(typer.echo, err=True)
    typer.secho(f"npdb: {diagnosis.title}", fg="red", bold=True, err=True)
    for line in redact(diagnosis.message).splitlines() or [""]:
        echo(f"  {line}")
    if diagnosis.hint:
        echo(f"  hint: {diagnosis.hint}")
    if docs:
        echo(f"  docs: {docs}")
    if debug:
        echo(redact("".join(traceback.format_exception(exc))).rstrip())
    if diagnosis.bug:
        echo("  This looks like a bug in npdb.")
    if report_path:
        echo(f"  report: {report_path} (credentials and home paths removed)")
        echo(f"  open an issue with it: {link}")
    elif diagnosis.exit_code not in (2, 130):
        echo(
            f"  If this looks wrong, rerun with {DEBUG_ENV}=1 to save a report for {ISSUES_URL}"
        )
    return diagnosis.exit_code
