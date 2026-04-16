"""
Tests for the GraphUpdater hot-reload functionality.
"""

import json
import pytest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from npdb.external.neurobagel.graph import GraphUpdater


class TestGraphUpdater:
    """Test suite for GraphUpdater class."""

    def test_init_defaults(self):
        """Test GraphUpdater initialization with defaults."""
        updater = GraphUpdater(username="user", password="pass")
        # Allow for both devcontainer and standalone defaults
        assert updater.graph_host in ["graphdb", "graph"]
        assert updater.graph_port == 7200
        assert updater.graph_db == "repositories/my_db"
        assert updater.base_url in [
            "http://graphdb:7200/repositories/my_db",
            "http://graph:7200/repositories/my_db"
        ]

    def test_init_custom_values(self):
        """Test GraphUpdater initialization with custom values."""
        updater = GraphUpdater(
            graph_host="remote-graphdb",
            graph_port=8888,
            graph_db="repositories/custom",
            username="admin",
            password="secret"
        )
        assert updater.graph_host == "remote-graphdb"
        assert updater.graph_port == 8888
        assert updater.graph_db == "repositories/custom"
        assert updater.base_url == "http://remote-graphdb:8888/repositories/custom"

    def test_get_auth_header_no_credentials(self):
        """Test auth header generation without credentials."""
        updater = GraphUpdater()
        assert updater._get_auth_header() is None

    def test_get_auth_header_with_credentials(self):
        """Test auth header generation with credentials."""
        updater = GraphUpdater(username="testuser", password="testpass")
        header = updater._get_auth_header()
        assert header is not None
        assert "Authorization" in header
        assert header["Authorization"].startswith("Basic ")

    def test_upload_jsonld_file_not_found(self):
        """Test upload fails when file doesn't exist."""
        updater = GraphUpdater(username="user", password="pass")
        nonexistent = Path("/tmp/nonexistent_file_12345.jsonld")

        with pytest.raises(FileNotFoundError):
            updater.upload_jsonld(nonexistent)

    @patch("npdb.external.neurobagel.graph.httpx.post")
    def test_upload_jsonld_success(self, mock_post):
        """Test successful JSON-LD upload."""
        # Create temporary JSON-LD file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonld', delete=False) as f:
            json.dump({"@context": {}, "@id": "test"}, f)
            temp_file = Path(f.name)

        try:
            # Mock successful response
            mock_response = Mock()
            mock_response.status_code = 204
            mock_post.return_value = mock_response

            updater = GraphUpdater(username="user", password="pass")
            result = updater.upload_jsonld(temp_file)

            assert result is True
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert "statements" in call_args[1]["headers"]["Content-Type"] or \
                   "application/ld+json" in call_args[1]["headers"]["Content-Type"]
        finally:
            temp_file.unlink()

    @patch("npdb.external.neurobagel.graph.httpx.post")
    def test_upload_jsonld_http_error(self, mock_post):
        """Test upload fails on HTTP error."""
        import httpx

        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonld', delete=False) as f:
            json.dump({"@context": {}, "@id": "test"}, f)
            temp_file = Path(f.name)

        try:
            # Create a real HTTPStatusError
            mock_response = Mock(spec=httpx.Response)
            mock_response.status_code = 400
            mock_response.text = "Invalid RDF"

            # Use httpx.HTTPStatusError which is a real class
            error = httpx.HTTPStatusError(
                "Bad Request", request=Mock(), response=mock_response)
            mock_post.side_effect = error

            updater = GraphUpdater(username="user", password="pass")
            result = updater.upload_jsonld(temp_file, verbose=False)

            assert result is False
        finally:
            temp_file.unlink()

    def test_update_datasets_metadata_new_file(self):
        """Test updating metadata when file doesn't exist yet."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_file = Path(tmpdir) / "datasets_metadata.json"
            jsonld_file = Path(tmpdir) / "test.jsonld"
            jsonld_file.write_text("{}")

            updater = GraphUpdater(username="user", password="pass")
            result = updater.update_datasets_metadata(
                metadata_file,
                jsonld_file,
                "dataset-001",
                {"dataset_name": "Test Dataset", "authors": ["Jane Doe"]},
                verbose=False
            )

            assert result is True
            assert metadata_file.exists()

            with open(metadata_file) as f:
                data = json.load(f)
            assert "dataset-001" in data
            assert data["dataset-001"]["dataset_name"] == "Test Dataset"

    def test_update_datasets_metadata_existing_file(self):
        """Test updating metadata with existing datasets."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_file = Path(tmpdir) / "datasets_metadata.json"
            jsonld_file = Path(tmpdir) / "test.jsonld"
            jsonld_file.write_text("{}")

            # Create existing metadata
            existing = {
                "existing-001": {"dataset_name": "Existing Dataset"}
            }
            with open(metadata_file, "w") as f:
                json.dump(existing, f)

            updater = GraphUpdater(username="user", password="pass")
            result = updater.update_datasets_metadata(
                metadata_file,
                jsonld_file,
                "dataset-002",
                {"dataset_name": "New Dataset"},
                verbose=False
            )

            assert result is True
            with open(metadata_file) as f:
                data = json.load(f)
            assert len(data) == 2
            assert "existing-001" in data
            assert "dataset-002" in data

    @patch("npdb.external.neurobagel.graph.httpx.post")
    def test_hot_reload_dataset_success(self, mock_post):
        """Test successful hot-reload of dataset."""
        with tempfile.TemporaryDirectory() as tmpdir:
            jsonld_file = Path(tmpdir) / "whole-spine.jsonld"
            metadata_file = Path(tmpdir) / "datasets_metadata.json"

            jsonld_file.write_text(json.dumps({"@context": {}, "@id": "test"}))

            # Mock successful GraphDB upload
            mock_response = Mock()
            mock_response.status_code = 204
            mock_post.return_value = mock_response

            updater = GraphUpdater(username="user", password="pass")
            result = updater.hot_reload_dataset(
                jsonld_file,
                "test-dataset",
                {"dataset_name": "Test Dataset"},
                metadata_file
            )

            assert result is True
            assert metadata_file.exists()

    @patch("npdb.external.neurobagel.graph.httpx.post")
    def test_hot_reload_dataset_without_metadata(self, mock_post):
        """Test hot-reload without metadata file update."""
        with tempfile.TemporaryDirectory() as tmpdir:
            jsonld_file = Path(tmpdir) / "whole-spine.jsonld"
            jsonld_file.write_text(json.dumps({"@context": {}, "@id": "test"}))

            mock_response = Mock()
            mock_response.status_code = 204
            mock_post.return_value = mock_response

            updater = GraphUpdater(username="user", password="pass")
            result = updater.hot_reload_dataset(
                jsonld_file,
                "test-dataset",
                {"dataset_name": "Test Dataset"},
                None  # No metadata file
            )

            assert result is True
