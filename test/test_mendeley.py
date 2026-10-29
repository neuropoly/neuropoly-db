import hashlib
import importlib
import io
import os
import zipfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import pytest
from typer.testing import CliRunner

from npdb.cli.observers import DownloadObserver
from npdb.factories import ProviderManagerFactory
from npdb.managers.mendeley import MendeleyProviderManager

PUBLIC = "https://data.mendeley.com/public-api/datasets/yd695ynbdz"
AUTH = "https://api.mendeley.com/datasets/yd695ynbdz"
DOWNLOAD = "https://data.mendeley.com/public-files/archive"
STORAGE = "https://storage.example.org/archive"


class Chunks(httpx.SyncByteStream):
    def __init__(self, content, fail=False):
        self.content = content
        self.fail = fail

    def __iter__(self):
        for start in range(0, len(self.content), 17):
            yield self.content[start : start + 17]
        if self.fail:
            raise httpx.ReadError("interrupted download")


@pytest.fixture
def transport(monkeypatch, tmp_path_factory):
    monkeypatch.delenv("NP_MENDELEY_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("NP_NPDB_CACHE_DIR", raising=False)
    monkeypatch.setenv(
        "XDG_CACHE_HOME", str(tmp_path_factory.mktemp("mendeley-default-cache"))
    )
    clients = []

    def install(handler):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        clients.append(client)
        monkeypatch.setattr(httpx, "get", client.get)
        monkeypatch.setattr(httpx, "stream", client.stream)

    yield install
    for client in clients:
        client.close()


def metadata(content, name="dataset.zip"):
    return {
        "files": [
            {
                "filename": name,
                "content_details": {
                    "download_url": DOWNLOAD,
                    "size": len(content),
                    "sha256_hash": hashlib.sha256(content).hexdigest(),
                },
            }
        ]
    }


def bundle():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            "Spinal_fMRI_UpperLimb/sub-13/ses-02/anat/sub-13_ses-02_T2w.nii.gz",
            b"image",
        )
        archive.writestr("__MACOSX/Spinal_fMRI_UpperLimb/._sub-13", b"metadata")
    return stream.getvalue()


@pytest.mark.parametrize("token", [None, "optional-token"])
def test_public_fetch_streams_redirect_and_selects_wrapped_bids_root(
    tmp_path, monkeypatch, transport, token
):
    monkeypatch.delenv("NP_MENDELEY_ACCESS_TOKEN", raising=False)
    content = bundle()
    requests = []

    def handler(request):
        requests.append(request)
        assert "authorization" not in request.headers
        if str(request.url) == PUBLIC:
            return httpx.Response(200, json=metadata(content))
        if str(request.url) == DOWNLOAD:
            return httpx.Response(302, headers={"Location": STORAGE})
        assert str(request.url) == STORAGE
        return httpx.Response(200, stream=Chunks(content))

    transport(handler)
    manager = MendeleyProviderManager(access_token=token)
    result = manager.fetch("yd695ynbdz", tmp_path)
    assert result == tmp_path / "Spinal_fMRI_UpperLimb"
    assert (result / "sub-13/ses-02/anat/sub-13_ses-02_T2w.nii.gz").read_bytes() == b"image"
    assert (result / "participants.tsv").read_text() == (
        "participant_id\tage\tsex\nsub-13\tN/A\tN/A\n"
    )
    assert not (tmp_path / "dataset.zip").exists()
    assert not (tmp_path / "derivatives").exists()
    assert manager.access_type == "public"
    assert [str(request.url) for request in requests] == [PUBLIC, DOWNLOAD, STORAGE]


@pytest.mark.parametrize("status", [401, 403, 404])
def test_restricted_metadata_uses_token_only_on_authenticated_endpoint(
    tmp_path, transport, status
):
    content = b"participant_id\tage\tsex\nsub-01\t30\tF\n"

    def handler(request):
        if str(request.url) == PUBLIC:
            assert "authorization" not in request.headers
            return httpx.Response(status)
        if str(request.url) == AUTH:
            assert request.headers["authorization"] == "Bearer access-token"
            return httpx.Response(200, json=metadata(content, "participants.tsv"))
        assert str(request.url) == DOWNLOAD
        assert "authorization" not in request.headers
        return httpx.Response(200, stream=Chunks(content))

    transport(handler)
    manager = MendeleyProviderManager(access_token="access-token")
    assert manager.fetch("yd695ynbdz", tmp_path) == tmp_path
    assert manager.access_type == "restricted"


@pytest.mark.parametrize("stage", ["metadata", "download", "authenticated"])
@pytest.mark.parametrize("status", [401, 403])
def test_access_denied_is_actionable(tmp_path, monkeypatch, transport, stage, status):
    monkeypatch.delenv("NP_MENDELEY_ACCESS_TOKEN", raising=False)

    def handler(request):
        if str(request.url) == PUBLIC and stage == "download":
            return httpx.Response(200, json=metadata(b"data"))
        return httpx.Response(status)

    transport(handler)
    manager = MendeleyProviderManager(
        access_token="invalid-token" if stage == "authenticated" else None
    )
    with pytest.raises(PermissionError, match="NP_MENDELEY_ACCESS_TOKEN"):
        manager.fetch("yd695ynbdz", tmp_path)
    assert not list(tmp_path.glob("*.zip"))
    assert not list(tmp_path.glob(".*.part"))


@pytest.mark.parametrize("failure", ["hash", "size", "stream", "http"])
def test_failed_download_preserves_previous_file_and_cleans_partial(
    tmp_path, transport, failure
):
    target = tmp_path / "dataset.zip"
    target.write_bytes(b"previous")
    payload = metadata(b"data")
    details = payload["files"][0]["content_details"]
    if failure == "hash":
        details["sha256_hash"] = "0" * 64
    if failure == "size":
        details["size"] += 1

    def handler(request):
        if str(request.url) == PUBLIC:
            return httpx.Response(200, json=payload)
        return httpx.Response(
            500 if failure == "http" else 200,
            stream=Chunks(b"data", fail=failure == "stream"),
        )

    transport(handler)
    with (
        patch.object(MendeleyProviderManager, "prepare_fetched") as prepare,
        pytest.raises((ValueError, httpx.ReadError, httpx.HTTPStatusError)),
    ):
        MendeleyProviderManager().fetch("yd695ynbdz", tmp_path)
    prepare.assert_not_called()
    assert target.read_bytes() == b"previous"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["dataset.zip"]


@pytest.mark.parametrize(
    "name", ["", "../outside", "/outside", "nested/file", "..", "a\\b", "a:b"]
)
def test_unsafe_download_names_are_rejected(tmp_path, transport, name):
    payload = metadata(b"data")
    payload["files"][0]["filename"] = name
    transport(lambda request: httpx.Response(200, json=payload))
    with pytest.raises(ValueError, match="Unsafe Mendeley filename"):
        MendeleyProviderManager().fetch("yd695ynbdz", tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("problem", ["no-files", "no-url", "duplicate"])
def test_incomplete_metadata_is_not_silently_skipped(tmp_path, transport, problem):
    payload = metadata(b"data", "participants.tsv")
    if problem == "no-files":
        payload["files"] = []
    elif problem == "no-url":
        del payload["files"][0]["content_details"]["download_url"]
    else:
        payload["files"] *= 2

    def handler(request):
        if str(request.url) == PUBLIC:
            return httpx.Response(200, json=payload)
        return httpx.Response(200, stream=Chunks(b"data"))

    transport(handler)
    with pytest.raises((ValueError, FileExistsError)):
        MendeleyProviderManager().fetch("yd695ynbdz", tmp_path)


def test_multiple_wrapped_datasets_are_rejected(tmp_path):
    for name in ("first", "second"):
        (tmp_path / name / "sub-01").mkdir(parents=True)
    with pytest.raises(ValueError, match="Multiple dataset roots"):
        MendeleyProviderManager().prepare(tmp_path)


def test_auxiliary_subject_examples_are_not_selected_as_datasets(tmp_path):
    actual = tmp_path / "dataset"
    (actual / "sub-01").mkdir(parents=True)
    for name in ("code", "phenotype", "stimuli", "sourcedata", "derivatives", "__MACOSX"):
        (tmp_path / name / "example/sub-99").mkdir(parents=True)
    assert MendeleyProviderManager().prepare(tmp_path) == actual
    assert "sub-99" not in (actual / "participants.tsv").read_text()


def test_flat_rawdata_and_existing_participants_are_preserved(tmp_path):
    rawdata = tmp_path / "rawdata"
    (rawdata / "sub-01").mkdir(parents=True)
    table = b"participant_id\tage\tsex\nsub-01\t30\tF\n"
    (rawdata / "participants.tsv").write_bytes(table)
    assert MendeleyProviderManager().prepare(tmp_path) == tmp_path
    assert (tmp_path / "participants.tsv").read_bytes() == table
    assert (tmp_path / "sub-01").is_dir()
    assert not rawdata.exists()


def test_corrupt_downloaded_archive_is_retained(tmp_path, transport):
    content = b"not a ZIP"

    def handler(request):
        if str(request.url) == PUBLIC:
            return httpx.Response(200, json=metadata(content))
        return httpx.Response(200, stream=Chunks(content))

    transport(handler)
    with pytest.raises(zipfile.BadZipFile):
        MendeleyProviderManager().fetch("yd695ynbdz", tmp_path)
    assert (tmp_path / "dataset.zip").read_bytes() == content


@pytest.mark.parametrize("status", [404, 500])
def test_metadata_http_errors_are_preserved(tmp_path, transport, status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status)

    transport(handler)
    with pytest.raises(httpx.HTTPStatusError) as error:
        MendeleyProviderManager().fetch("yd695ynbdz", tmp_path)
    assert error.value.response.status_code == status
    assert len(requests) == 1


@pytest.mark.parametrize("cache_option", ["none", "cli", "env"])
def test_cli_stages_inside_output_and_converts_selected_root(
    tmp_path, transport, monkeypatch, cache_option
):
    module = importlib.import_module("npdb.cli.cli")
    content = bundle()

    def handler(request):
        if str(request.url) == PUBLIC:
            return httpx.Response(200, json=metadata(content))
        return httpx.Response(200, stream=Chunks(content))

    transport(handler)
    output = tmp_path / "out"
    cache = tmp_path / "cache"
    options = []
    if cache_option == "cli":
        options = ["--cache-dir", str(cache)]
    elif cache_option == "env":
        monkeypatch.setenv("NP_NPDB_CACHE_DIR", str(cache))
    expected = output / "mendeley_yd695ynbdz/Spinal_fMRI_UpperLimb"
    with patch.object(module, "local2bagel") as convert:
        result = CliRunner().invoke(
            module.npdb,
            ["convert", "bagel", "mendeley", "yd695ynbdz", str(output), "--mode", "auto"]
            + options,
        )
    assert result.exit_code == 0, result.exception
    assert convert.call_args.kwargs["input_dir"] == expected
    assert convert.call_args.kwargs["output"] == output
    assert convert.call_args.kwargs["access_type"] == "public"
    assert (expected / "participants.tsv").exists()
    if cache_option != "none":
        assert next(cache.rglob("dataset.zip")).read_bytes() == content
    else:
        assert not cache.exists()


def serve(transport, payload, content, *, fail=False):
    requests = []

    def handler(request):
        requests.append(request)
        if str(request.url) == PUBLIC:
            return httpx.Response(200, json=payload)
        return httpx.Response(200, stream=Chunks(content, fail=fail))

    transport(handler)
    return requests


def test_cache_survives_extraction_and_reuses_content_across_redirect_urls(
    tmp_path, transport
):
    content = bundle()
    payload = metadata(content)
    requests = serve(transport, payload, content)
    cache = tmp_path / "cache"
    manager = MendeleyProviderManager(cache_dir=cache)
    observer = Mock(spec=DownloadObserver)
    manager.add_download_observer(observer)
    first_root = manager.fetch("yd695ynbdz", tmp_path / "first")
    cached = next(cache.rglob("dataset.zip"))
    assert cached.read_bytes() == content
    assert not (tmp_path / "first/dataset.zip").exists()
    assert (first_root / "participants.tsv").exists()
    requests.clear()
    payload["files"][0]["content_details"]["download_url"] = "https://new.example/file"
    second_root = manager.fetch("yd695ynbdz", tmp_path / "second")
    assert [str(request.url) for request in requests] == [PUBLIC]
    assert (second_root / "participants.tsv").read_bytes() == (
        first_root / "participants.tsv"
    ).read_bytes()
    assert cached.read_bytes() == content
    assert len(list(cache.rglob("dataset.zip"))) == 1
    assert not list(cache.rglob("*.part"))
    messages = [call.args[1] for call in observer.on_repo_step.call_args_list]
    assert "Verifying cache: dataset.zip" in messages
    assert "Cache hit: dataset.zip" in messages
    assert "Staging cached file: dataset.zip" in messages
    observer.on_file_progress.assert_any_call("mendeley", "dataset.zip", 0, len(content))
    observer.on_file_progress.assert_any_call(
        "mendeley", "dataset.zip", len(content), len(content)
    )
    observer.on_repo_done.assert_called_with("mendeley", True)


def test_preparation_failure_keeps_cache_for_retry(tmp_path, transport):
    content = bundle()
    requests = serve(transport, metadata(content), content)
    manager = MendeleyProviderManager()
    cache = manager.cache_dir
    with (
        patch.object(manager, "prepare_fetched", side_effect=ValueError("preparation")),
        pytest.raises(ValueError, match="preparation"),
    ):
        manager.fetch("yd695ynbdz", tmp_path / "out")
    assert next(cache.rglob("dataset.zip")).read_bytes() == content
    requests.clear()
    root = MendeleyProviderManager().fetch("yd695ynbdz", tmp_path / "out")
    assert [str(request.url) for request in requests] == [PUBLIC]
    assert (root / "participants.tsv").exists()
    requests.clear()
    MendeleyProviderManager().fetch("yd695ynbdz", tmp_path / "retry")
    assert [str(request.url) for request in requests] == [PUBLIC]
    assert next(cache.rglob("dataset.zip")).read_bytes() == content


@pytest.mark.parametrize("corruption", ["same-size", "truncated"])
@pytest.mark.parametrize("valid_target", [True, False])
def test_corrupt_cache_is_recovered_from_verified_target_or_download(
    tmp_path, transport, corruption, valid_target
):
    content = b"participant_id\nsub-01\n"
    payload = metadata(content, "participants.tsv")
    requests = serve(transport, payload, content)
    cache = tmp_path / "cache"
    manager = MendeleyProviderManager(cache_dir=cache)
    manager.fetch("yd695ynbdz", tmp_path / "out")
    cached = next(cache.rglob("participants.tsv"))
    cached.write_bytes(b"x" * len(content) if corruption == "same-size" else b"x")
    if not valid_target:
        (tmp_path / "out/participants.tsv").write_bytes(b"x" * len(content))
    requests.clear()
    manager.fetch("yd695ynbdz", tmp_path / "out")
    assert [str(request.url) for request in requests] == (
        [PUBLIC] if valid_target else [PUBLIC, DOWNLOAD]
    )
    assert cached.read_bytes() == content
    assert (tmp_path / "out/participants.tsv").read_bytes() == content


def test_metadata_changes_do_not_reuse_stale_cache(tmp_path, transport):
    content = b"participant_id\nsub-01\n"
    payload = metadata(content, "participants.tsv")
    requests = serve(transport, payload, content)
    cache = tmp_path / "cache"
    manager = MendeleyProviderManager(cache_dir=cache)
    manager.fetch("yd695ynbdz", tmp_path / "out")
    content = b"participant_id\nsub-02\n"
    payload = metadata(content, "participants.tsv")
    requests = serve(transport, payload, content)
    manager.fetch("yd695ynbdz", tmp_path / "out")
    assert [str(request.url) for request in requests] == [PUBLIC, DOWNLOAD]
    assert (tmp_path / "out/participants.tsv").read_bytes() == content
    assert len(list(cache.rglob("participants.tsv"))) == 2


@pytest.mark.parametrize("missing", ["size", "sha256_hash", "both"])
def test_insufficient_integrity_metadata_bypasses_cache(
    tmp_path, transport, missing
):
    content = b"participant_id\nsub-01\n"
    payload = metadata(content, "participants.tsv")
    for key in ("size", "sha256_hash") if missing == "both" else (missing,):
        del payload["files"][0]["content_details"][key]
    requests = serve(transport, payload, content)
    cache = tmp_path / "cache"
    manager = MendeleyProviderManager(cache_dir=cache)
    observer = Mock(spec=DownloadObserver)
    manager.add_download_observer(observer)
    manager.fetch("yd695ynbdz", tmp_path / "out")
    manager.fetch("yd695ynbdz", tmp_path / "out")
    assert [str(request.url) for request in requests] == [
        PUBLIC, DOWNLOAD, PUBLIC, DOWNLOAD
    ]
    assert not list(cache.rglob("participants.tsv"))
    assert any(
        "cache bypassed" in call.args[1]
        for call in observer.on_repo_step.call_args_list
    )


def test_interrupted_cache_download_does_not_publish_partial(tmp_path, transport):
    content = bundle()
    payload = metadata(content)
    serve(transport, payload, content, fail=True)
    cache = tmp_path / "cache"
    output = tmp_path / "out"
    output.mkdir()
    (output / "dataset.zip").write_bytes(b"previous")
    manager = MendeleyProviderManager(cache_dir=cache)
    with pytest.raises(httpx.ReadError):
        manager.fetch("yd695ynbdz", output)
    assert not list(cache.rglob("*.zip"))
    assert not list(cache.rglob("*.part"))
    assert (output / "dataset.zip").read_bytes() == b"previous"
    requests = serve(transport, payload, content)
    manager.fetch("yd695ynbdz", output)
    assert [str(request.url) for request in requests] == [PUBLIC, DOWNLOAD]
    assert next(cache.rglob("dataset.zip")).read_bytes() == content


@pytest.mark.parametrize("failure", ["io", "integrity"])
def test_failed_staging_preserves_cache_and_previous_destination(
    tmp_path, transport, failure
):
    content = b"participant_id\nsub-01\n"
    requests = serve(transport, metadata(content, "participants.tsv"), content)
    cache = tmp_path / "cache"
    manager = MendeleyProviderManager(cache_dir=cache)
    manager.fetch("yd695ynbdz", tmp_path / "first")
    output = tmp_path / "second"
    output.mkdir()
    target = output / "participants.tsv"
    target.write_bytes(b"previous")
    requests.clear()

    def broken_copy(source, destination, *, length):
        destination.write(b"wrong data")
        if failure == "io":
            raise OSError("disk full")

    with (
        patch("npdb.managers.mendeley.shutil.copyfileobj", side_effect=broken_copy),
        pytest.raises(OSError if failure == "io" else ValueError),
    ):
        manager.fetch("yd695ynbdz", output)
    assert target.read_bytes() == b"previous"
    assert next(cache.rglob("participants.tsv")).read_bytes() == content
    assert not list(output.glob("*.part"))
    assert [str(request.url) for request in requests] == [PUBLIC]


@pytest.mark.parametrize("overlap", ["same", "cache-inside", "output-inside"])
def test_cache_and_output_overlap_is_rejected(tmp_path, transport, overlap):
    cache = tmp_path / "root"
    output = cache
    if overlap == "cache-inside":
        cache = output / "cache"
    elif overlap == "output-inside":
        output = cache / "out"
    requests = serve(transport, metadata(bundle()), bundle())
    with pytest.raises(ValueError, match="must not overlap"):
        MendeleyProviderManager(cache_dir=cache).fetch("yd695ynbdz", output)
    assert not requests


@pytest.mark.parametrize("destination", ["cache", "cache-entry", "output", "target"])
def test_symlink_destinations_are_rejected(tmp_path, transport, destination):
    cache = tmp_path / "cache"
    output = tmp_path / "out"
    outside = tmp_path / "outside"
    outside.mkdir()
    content = b"participant_id\nsub-01\n"
    requests = serve(transport, metadata(content, "participants.tsv"), content)
    manager = MendeleyProviderManager(cache_dir=cache)
    if destination == "cache-entry":
        manager.fetch("yd695ynbdz", output)
        entry = next(cache.rglob("participants.tsv"))
        entry.unlink()
        entry.symlink_to(outside / "target")
        requests.clear()
    elif destination == "cache":
        cache.symlink_to(outside, target_is_directory=True)
    elif destination == "output":
        output.symlink_to(outside, target_is_directory=True)
    else:
        output.mkdir()
        (output / "participants.tsv").symlink_to(outside / "target")
    with pytest.raises(ValueError, match="symlink destination"):
        manager.fetch("yd695ynbdz", output)
    assert not list(outside.iterdir())
    assert all(str(request.url) == PUBLIC for request in requests)


def test_disk_space_checks_include_cache_and_staging(tmp_path, transport):
    content = b"participant_id\nsub-01\n"
    requests = serve(transport, metadata(content, "participants.tsv"), content)
    cache = tmp_path / "cache"
    manager = MendeleyProviderManager(cache_dir=cache)
    with (
        patch(
            "npdb.managers.mendeley.shutil.disk_usage",
            return_value=SimpleNamespace(free=2 * len(content) - 1),
        ),
        pytest.raises(OSError, match=f"need {2 * len(content)} bytes"),
    ):
        manager.fetch("yd695ynbdz", tmp_path / "out")
    assert [str(request.url) for request in requests] == [PUBLIC]
    manager.fetch("yd695ynbdz", tmp_path / "out")
    requests.clear()
    with patch(
        "npdb.managers.mendeley.shutil.disk_usage",
        return_value=SimpleNamespace(free=len(content)),
    ):
        manager.fetch("yd695ynbdz", tmp_path / "second")
    assert [str(request.url) for request in requests] == [PUBLIC]


def test_separate_cache_volume_is_checked_independently(tmp_path, transport):
    cache = tmp_path / "cache"
    cache.mkdir()
    output = tmp_path / "out"
    content = b"participant_id\nsub-01\n"
    requests = serve(transport, metadata(content, "participants.tsv"), content)
    original_stat = type(cache).stat
    spaces = []

    def stat(path, *, follow_symlinks=True):
        result = original_stat(path, follow_symlinks=follow_symlinks)
        if path == cache and follow_symlinks:
            fields = list(result)
            fields[2] = result.st_dev + 1
            return os.stat_result(fields)
        return result

    def disk_usage(path):
        spaces.append(path)
        return SimpleNamespace(free=len(content) if path == output else len(content) - 1)

    with (
        patch.object(type(cache), "stat", stat),
        patch("npdb.managers.mendeley.shutil.disk_usage", side_effect=disk_usage),
        pytest.raises(OSError, match=f"need {len(content)} bytes"),
    ):
        MendeleyProviderManager(cache_dir=cache).fetch("yd695ynbdz", output)
    assert spaces == [output, cache]
    assert [str(request.url) for request in requests] == [PUBLIC]


@pytest.mark.parametrize(
    "key,value",
    [
        ("size", -1),
        ("size", True),
        ("size", "10"),
        ("sha256_hash", "invalid"),
        ("sha256_hash", 1),
    ],
)
def test_invalid_integrity_metadata_fails_before_download(
    tmp_path, transport, key, value
):
    content = b"participant_id\nsub-01\n"
    payload = metadata(content, "participants.tsv")
    payload["files"][0]["content_details"][key] = value
    requests = serve(transport, payload, content)
    cache = tmp_path / "cache"
    with pytest.raises(ValueError, match="Invalid Mendeley"):
        MendeleyProviderManager(cache_dir=cache).fetch("yd695ynbdz", tmp_path / "out")
    assert [str(request.url) for request in requests] == [PUBLIC]
    assert not list(cache.rglob("participants.tsv"))


def test_cache_does_not_bypass_authorization(tmp_path, transport):
    content = b"participant_id\nsub-01\n"
    cache = tmp_path / "cache"
    serve(transport, metadata(content, "participants.tsv"), content)
    MendeleyProviderManager(cache_dir=cache).fetch("yd695ynbdz", tmp_path / "first")
    requests = []

    def deny(request):
        requests.append(request)
        return httpx.Response(403)

    transport(deny)
    with pytest.raises(PermissionError):
        MendeleyProviderManager(cache_dir=cache).fetch("yd695ynbdz", tmp_path / "second")
    assert [str(request.url) for request in requests] == [PUBLIC]
    assert not list((tmp_path / "second").iterdir())


def test_factory_wires_cache_and_explicit_path_overrides_environment(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("NP_NPDB_CACHE_DIR", str(tmp_path / "env-cache"))
    explicit = ProviderManagerFactory.create("mendeley", cache_dir=tmp_path / "cache")
    assert explicit.cache_dir == tmp_path / "cache"
    from_env = ProviderManagerFactory.create("mendeley")
    assert from_env.cache_dir == tmp_path / "env-cache"


@pytest.mark.parametrize("xdg", [True, False])
def test_default_cache_uses_xdg_or_home(tmp_path, monkeypatch, xdg):
    monkeypatch.delenv("NP_NPDB_CACHE_DIR", raising=False)
    monkeypatch.setattr("npdb.managers.mendeley.Path.home", lambda: tmp_path / "home")
    if xdg:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
        root = tmp_path / "xdg"
    else:
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        root = tmp_path / "home/.cache"
    assert MendeleyProviderManager().cache_dir == root / "npdb/downloads"


def test_verified_existing_archive_is_adopted_without_download(tmp_path, transport):
    content = bundle()
    requests = serve(transport, metadata(content), content)
    output = tmp_path / "out"
    output.mkdir()
    (output / "dataset.zip").write_bytes(content)
    manager = MendeleyProviderManager()
    observer = Mock(spec=DownloadObserver)
    manager.add_download_observer(observer)
    with patch(
        "npdb.managers.mendeley.shutil.disk_usage",
        return_value=SimpleNamespace(free=len(content)),
    ):
        root = manager.fetch("yd695ynbdz", output)
    assert [str(request.url) for request in requests] == [PUBLIC]
    assert (root / "participants.tsv").exists()
    assert next(manager.cache_dir.rglob("dataset.zip")).read_bytes() == content
    assert not (output / "dataset.zip").exists()
    assert any(
        "Reusing verified download" in call.args[1]
        for call in observer.on_repo_step.call_args_list
    )


def test_cli_default_cache_survives_downstream_conversion_failure(tmp_path, transport):
    module = importlib.import_module("npdb.cli.cli")
    content = bundle()
    requests = serve(transport, metadata(content), content)
    command = [
        "convert", "bagel", "mendeley", "yd695ynbdz", str(tmp_path / "out"),
        "--mode", "full-auto", "--headless", "--bids-version", "latest",
    ]
    with patch.object(
        module, "local2bagel", side_effect=[ValueError("after download"), None]
    ):
        failed = CliRunner().invoke(module.npdb, command)
        assert failed.exit_code == 1
        assert "after download" in failed.output
        completed = CliRunner().invoke(module.npdb, command)
        assert completed.exit_code == 0, completed.exception
    assert [str(request.url) for request in requests] == [PUBLIC, DOWNLOAD, PUBLIC]
