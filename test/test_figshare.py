import io
import zipfile
from unittest.mock import Mock, call, patch

import httpx
import pytest

from npdb.managers.figshare import FigshareProviderManager


@pytest.fixture
def download_only(monkeypatch):
    monkeypatch.setattr(
        FigshareProviderManager, "prepare_fetched", lambda self, path: path
    )


def _response(payload=None, content=b""):
    response = Mock()
    response.json.return_value = payload
    response.content = content
    response.raise_for_status.return_value = None
    return response


def test_fetch_article_id_does_not_search(tmp_path, download_only):
    article = _response(
        {"files": [{"name": "dataset.tsv", "download_url": "file-url"}]}
    )
    download = _response(content=b"contents")
    manager = FigshareProviderManager(token="token")

    with (
        patch("npdb.managers.figshare.httpx.post") as post,
        patch(
            "npdb.managers.figshare.httpx.get", side_effect=[article, download]
        ) as get,
    ):
        result = manager.fetch("12345", tmp_path)

    post.assert_not_called()
    assert get.call_args_list[0] == call(
        "https://api.figshare.com/v2/articles/12345",
        headers={"Accept": "application/json", "Authorization": "token token"},
        timeout=30,
    )
    assert result == tmp_path
    assert (tmp_path / "dataset.tsv").read_bytes() == b"contents"


def test_fetch_collection_doi_downloads_each_article(tmp_path, download_only):
    collection_search = _response([{"id": 123}, {"id": 456}])
    article_1 = _response(
        {"files": [{"name": "first.tsv", "download_url": "first-url"}]}
    )
    article_2 = _response(
        {"files": [{"name": "second.tsv", "download_url": "second-url"}]}
    )
    download_1 = _response(content=b"first")
    download_2 = _response(content=b"second")
    manager = FigshareProviderManager()
    prepared = tmp_path / "prepared"

    def prepare(path):
        assert path == tmp_path
        assert (path / "first.tsv").read_bytes() == b"first"
        assert (path / "second.tsv").read_bytes() == b"second"
        return prepared

    with (
        patch("npdb.managers.figshare.httpx.post") as post,
        patch(
            "npdb.managers.figshare.httpx.get",
            side_effect=[
                collection_search,
                article_1,
                download_1,
                article_2,
                download_2,
            ],
        ) as get,
        patch.object(manager, "prepare_fetched", side_effect=prepare) as preparation,
    ):
        result = manager.fetch("10.6084/m9.figshare.c.7372564", tmp_path)

    assert result == prepared
    preparation.assert_called_once_with(tmp_path)
    post.assert_not_called()
    assert get.call_args_list[0] == call(
        "https://api.figshare.com/v2/collections/7372564/articles",
        params={"page": 1, "page_size": 100},
        headers={"Accept": "application/json"},
        timeout=30,
    )
    assert [get.call_args_list[index] for index in (1, 3)] == [
        call(
            "https://api.figshare.com/v2/articles/123",
            headers={"Accept": "application/json"},
            timeout=30,
        ),
        call(
            "https://api.figshare.com/v2/articles/456",
            headers={"Accept": "application/json"},
            timeout=30,
        ),
    ]
    assert (tmp_path / "first.tsv").read_bytes() == b"first"
    assert (tmp_path / "second.tsv").read_bytes() == b"second"


def test_fetch_article_doi_falls_back_to_exact_doi_search(tmp_path, download_only):
    no_collection = _response([])
    article_search = _response([{"id": 123, "doi": "10.6084/m9.figshare.123"}])
    article = _response({"files": []})
    manager = FigshareProviderManager()

    with (
        patch(
            "npdb.managers.figshare.httpx.post",
            side_effect=[no_collection, article_search],
        ) as post,
        patch("npdb.managers.figshare.httpx.get", return_value=article),
    ):
        manager.fetch("https://doi.org/10.6084/m9.figshare.123", tmp_path)

    assert post.call_count == 2
    assert (
        post.call_args_list[1].kwargs["json"]["search_for"] == "10.6084/m9.figshare.123"
    )


def test_collection_prepares_shared_dataset_after_all_component_archives(tmp_path):
    masks = io.BytesIO()
    with zipfile.ZipFile(masks, "w") as archive:
        archive.writestr("markers/sub-01_mask.nii.gz", b"mask")
    rawdata = io.BytesIO()
    with zipfile.ZipFile(rawdata, "w") as archive:
        archive.writestr("rawdata/sub-01/anat/sub-01_T2w.nii.gz", b"first")
        archive.writestr("rawdata/sub-02/anat/sub-02_T2w.nii.gz", b"second")
    manager = FigshareProviderManager()
    with (
        patch(
            "npdb.managers.figshare.httpx.get",
            side_effect=[
                _response([{"id": 123}, {"id": 456}]),
                _response(
                    {"files": [{"name": "markers.zip", "download_url": "markers-url"}]}
                ),
                _response(content=masks.getvalue()),
                _response(
                    {"files": [{"name": "rawdata.zip", "download_url": "rawdata-url"}]}
                ),
                _response(content=rawdata.getvalue()),
            ],
        ),
        patch.object(
            manager, "prepare_fetched", wraps=manager.prepare_fetched
        ) as preparation,
    ):
        result = manager.fetch("10.6084/m9.figshare.c.7372564", tmp_path)
    preparation.assert_called_once_with(tmp_path)
    assert result == tmp_path
    assert (result / "sub-01/anat/sub-01_T2w.nii.gz").read_bytes() == b"first"
    assert (result / "sub-02/anat/sub-02_T2w.nii.gz").read_bytes() == b"second"
    assert (result / "derivatives/markers/sub-01_mask.nii.gz").read_bytes() == b"mask"
    assert (result / "participants.tsv").read_text() == (
        "participant_id\tage\tsex\nsub-01\tN/A\tN/A\nsub-02\tN/A\tN/A\n"
    )


def test_fetch_unknown_doi_raises_clear_error(tmp_path):
    with patch(
        "npdb.managers.figshare.httpx.post",
        side_effect=[_response([]), _response([])],
    ):
        with pytest.raises(ValueError, match="No public Figshare articles found"):
            FigshareProviderManager().fetch("10.6084/m9.figshare.unknown", tmp_path)


@pytest.mark.parametrize(
    "identifier",
    [
        "10.6084/m9.figshare.c.7372564",
        "https://doi.org/10.6084/m9.figshare.c.7372564",
        "doi: 10.6084/m9.figshare.c.7372564",
    ],
)
def test_collection_resolves_actual_member_ids(identifier):
    member_ids = [26403595, 26403442, 26403565, 26403262]
    # Collection members have no resource_doi linking them to the collection.
    members = [{"id": article_id, "resource_doi": None} for article_id in member_ids]
    with (
        patch(
            "npdb.managers.figshare.httpx.get", return_value=_response(members)
        ) as get,
        patch("npdb.managers.figshare.httpx.post") as post,
    ):
        result = FigshareProviderManager()._article_ids(
            identifier, {"Accept": "application/json"}
        )
    assert result == member_ids
    assert "/collections/7372564/articles" in get.call_args.args[0]
    post.assert_not_called()


def test_collection_articles_are_paginated():
    first_page = [{"id": article_id} for article_id in range(100)]
    headers = {"Authorization": "token test-token"}
    with patch(
        "npdb.managers.figshare.httpx.get",
        side_effect=[_response(first_page), _response([{"id": 100}])],
    ) as get:
        result = FigshareProviderManager()._article_ids(
            "10.6084/m9.figshare.c.7372564", headers
        )
    assert result == list(range(101))
    assert [c.kwargs["params"]["page"] for c in get.call_args_list] == [1, 2]
    assert all(c.kwargs["headers"] == headers for c in get.call_args_list)


def test_empty_collection_raises_clear_error():
    with (
        patch("npdb.managers.figshare.httpx.get", return_value=_response([])),
        patch("npdb.managers.figshare.httpx.post") as post,
        pytest.raises(ValueError, match="No public Figshare articles found"),
    ):
        FigshareProviderManager()._article_ids("10.6084/m9.figshare.c.7372564", {})
    post.assert_not_called()


def test_collection_http_failure_is_propagated():
    response = httpx.Response(
        404,
        request=httpx.Request(
            "GET", "https://api.figshare.com/v2/collections/7372564/articles"
        ),
    )
    with (
        patch("npdb.managers.figshare.httpx.get", return_value=response),
        pytest.raises(httpx.HTTPStatusError),
    ):
        FigshareProviderManager()._article_ids("10.6084/m9.figshare.c.7372564", {})


def test_existing_download_can_be_refetched(tmp_path, download_only):
    (tmp_path / "README").write_bytes(b"previous")
    with patch(
        "npdb.managers.figshare.httpx.get",
        side_effect=[
            _response({"files": [{"name": "README", "download_url": "file-url"}]}),
            _response(content=b"updated"),
        ],
    ):
        FigshareProviderManager().fetch("26403262", tmp_path)
    assert (tmp_path / "README").read_bytes() == b"updated"


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"unexpected": "object"}, "Unexpected response"),
        ([{"doi": "10.6084/m9.figshare.123"}], "without an ID"),
    ],
)
def test_malformed_collection_response_raises_clear_error(payload, message):
    with (
        patch("npdb.managers.figshare.httpx.get", return_value=_response(payload)),
        pytest.raises(ValueError, match=message),
    ):
        FigshareProviderManager()._article_ids("10.6084/m9.figshare.c.7372564", {})


def test_duplicate_names_do_not_overwrite_another_article(tmp_path):
    article = _response({"files": [{"name": "README", "download_url": "file-url"}]})
    with (
        patch(
            "npdb.managers.figshare.httpx.get",
            side_effect=[
                _response([{"id": 123}, {"id": 456}]),
                article,
                _response(content=b"first"),
                article,
                _response(content=b"second"),
            ],
        ),
        pytest.raises(FileExistsError, match="Multiple Figshare files"),
    ):
        FigshareProviderManager().fetch("10.6084/m9.figshare.c.7372564", tmp_path)
    assert (tmp_path / "README").read_bytes() == b"first"


@pytest.mark.parametrize("final_status", [200, 403])
def test_download_follows_redirects(tmp_path, final_status, download_only):
    download_url = "https://ndownloader.figshare.com/files/48013288"
    storage_url = "https://storage.example/rawdata.zip?signature=test"
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.host == "api.figshare.com":
            return httpx.Response(
                200,
                json={"files": [{"name": "rawdata.zip", "download_url": download_url}]},
            )
        if str(request.url) == download_url:
            return httpx.Response(302, headers={"Location": storage_url})
        assert str(request.url) == storage_url
        assert "Authorization" not in request.headers
        return httpx.Response(final_status, content=b"archive contents")

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as client,
        patch("npdb.managers.figshare.httpx.get", side_effect=client.get),
    ):
        manager = FigshareProviderManager(token="test-token")
        if final_status == 200:
            manager.fetch("26403595", tmp_path)
            assert (tmp_path / "rawdata.zip").read_bytes() == b"archive contents"
        else:
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                manager.fetch("26403595", tmp_path)
            assert exc_info.value.response.status_code == final_status
            assert not (tmp_path / "rawdata.zip").exists()

    assert [str(request.url) for request in requests] == [
        "https://api.figshare.com/v2/articles/26403595",
        download_url,
        storage_url,
    ]
