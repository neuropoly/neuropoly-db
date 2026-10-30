"""Live ``npdb convert bagel <provider>`` runs compared against JSON-LD snapshots.

Opt-in: set ``NPDB_RUN_INTEGRATION=1``. Downloads go through a persistent cache
(``NPDB_INTEGRATION_CACHE_DIR``, default ``~/.cache/npdb/integration``) and the
annotation step drives the real headless Neurobagel annotation tool. Set
``NPDB_UPDATE_SNAPSHOTS=1`` to rewrite the snapshots from the live output.
"""

import difflib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from bagel.models import Dataset

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("NPDB_RUN_INTEGRATION") != "1",
        reason="set NPDB_RUN_INTEGRATION=1 to run live provider conversions",
    ),
]

SNAPSHOT_DIR = Path(__file__).parents[1] / "snapshots" / "convert"
LOCAL_FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "datasets" / "integration_local_bids"
)
NB_UUID = re.compile(r"nb:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
CONVERSION_TIMEOUT_S = 3 * 60 * 60


@dataclass(frozen=True)
class ConvertCase:
    provider: str
    arguments: tuple[str, ...]
    options: tuple[str, ...] = ()
    required_env: tuple[str, ...] = ()
    snapshot_optional: bool = False


CASES = [
    ConvertCase(
        "local",
        ("{local}", "https://example.org/npdb-integration-local"),
        ("--access-type", "public"),
    ),
    ConvertCase("git", ("https://github.com/OpenNeuroDatasets/ds000102/tree/00001",)),
    # Metadata-only fetch: imaging files become placeholders, so ~100 kB moves.
    ConvertCase("openneuro", ("ds000102",), ("--tag", "00001")),
    # spine-generic r20201130 stores multi-echo EchoTime as a string in six
    # T2star sidecars; that dataset error must not block the conversion.
    ConvertCase(
        "zenodo",
        ("4299140",),
        ("--cache-dir", "{cache}", "--bids-severity-threshold", "error"),
    ),
    ConvertCase("figshare", ("33212928",), ("--cache-dir", "{cache}")),
    ConvertCase(
        "mendeley",
        ("yd695ynbdz",),
        (
            "--cache-dir",
            "{cache}",
            "--bids-version",
            "1.9.0",
            "--bids-severity-threshold",
            "error",
        ),
    ),
    ConvertCase(
        "midrc",
        ("dg.MD1R/00001e99-cdd5-43d1-871a-09b6f9df5dad",),
        ("--cache-dir", "{cache}"),
        required_env=("NP_MIDRC_CREDENTIALS",),
        snapshot_optional=True,
    ),
]


def _cache_dir() -> Path:
    configured = os.environ.get("NPDB_INTEGRATION_CACHE_DIR")
    cache = (
        Path(configured).expanduser()
        if configured
        else Path.home() / ".cache" / "npdb" / "integration"
    )
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def _npdb_command() -> list[str]:
    script = Path(sys.executable).with_name("npdb")
    if script.is_file():
        return [str(script)]
    return [sys.executable, "-c", "from npdb.cli.cli import run; run()"]


def normalize_jsonld(document: dict) -> str:
    """Replace per-run random ``nb:<uuid>`` identifiers in order of appearance."""
    text = json.dumps(document, indent=2, ensure_ascii=False)
    placeholders: dict[str, str] = {}

    def placeholder(match: re.Match[str]) -> str:
        return placeholders.setdefault(
            match.group(0), f"nb:uuid-{len(placeholders) + 1:05d}"
        )

    return NB_UUID.sub(placeholder, text) + "\n"


def _run_conversion(case: ConvertCase, tmp_path: Path) -> Path:
    local_copy = tmp_path / "integration_local_bids"
    if case.provider == "local":
        shutil.copytree(LOCAL_FIXTURE, local_copy)

    output = tmp_path / "output"
    substitutions = {"local": str(local_copy), "cache": str(_cache_dir())}
    command = [
        *_npdb_command(),
        "convert",
        "bagel",
        case.provider,
        *(argument.format(**substitutions) for argument in case.arguments),
        str(output),
        *(option.format(**substitutions) for option in case.options),
        "--mode",
        "full-auto",
        "--headless",
    ]
    environment = {**os.environ, "NP_NPDB_CACHE_DIR": substitutions["cache"]}

    result = subprocess.run(
        command,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=CONVERSION_TIMEOUT_S,
    )
    assert result.returncode == 0, (
        f"{' '.join(command)} exited with {result.returncode}\n"
        f"--- stdout ---\n{result.stdout[-6000:]}\n"
        f"--- stderr ---\n{result.stderr[-6000:]}"
    )

    produced = sorted(output.glob("*.jsonld"))
    assert len(produced) == 1, f"Expected one JSON-LD file, found {produced}"
    return produced[0]


def _discard_fetched_data(output: Path) -> None:
    """Drop staged datasets; pytest keeps recent temp dirs and they can be GBs."""
    for entry in output.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)


def _check_produced_dataset(document: dict, raw_text: str, tmp_path: Path) -> None:
    Dataset.model_validate(
        {key: value for key, value in document.items() if key != "@context"}
    )

    assert document["hasSamples"], "No subjects were converted"
    for subject in document["hasSamples"]:
        assert subject["hasSession"], f"{subject['hasLabel']} has no sessions"
    assert str(tmp_path) not in raw_text, "A temporary path leaked into the output"


@pytest.mark.parametrize("case", CASES, ids=[case.provider for case in CASES])
def test_convert_matches_snapshot(case: ConvertCase, tmp_path: Path) -> None:
    missing = [name for name in case.required_env if not os.environ.get(name)]
    if missing:
        pytest.skip(f"{case.provider} needs {', '.join(missing)}")

    snapshot = SNAPSHOT_DIR / f"{case.provider}.jsonld"
    updating = os.environ.get("NPDB_UPDATE_SNAPSHOTS") == "1"
    if not snapshot.exists() and not updating:
        if case.snapshot_optional:
            pytest.skip(f"No {case.provider} snapshot recorded yet")
        pytest.fail(f"Missing snapshot {snapshot}; rerun with NPDB_UPDATE_SNAPSHOTS=1")

    produced = _run_conversion(case, tmp_path)
    raw_text = produced.read_text(encoding="utf-8")
    _discard_fetched_data(produced.parent)
    document = json.loads(raw_text)
    _check_produced_dataset(document, raw_text, tmp_path)
    normalized = normalize_jsonld(document)

    if updating:
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(normalized, encoding="utf-8")

    expected = snapshot.read_text(encoding="utf-8")
    if normalized != expected:
        diff = difflib.unified_diff(
            expected.splitlines(),
            normalized.splitlines(),
            fromfile=f"snapshot/{snapshot.name}",
            tofile=f"live/{produced.name}",
            lineterm="",
        )
        shown = "\n".join(line for _, line in zip(range(200), diff))
        pytest.fail(
            f"{case.provider} JSON-LD differs from its snapshot. Rerun with "
            f"NPDB_UPDATE_SNAPSHOTS=1 if the change is intended.\n{shown}"
        )
