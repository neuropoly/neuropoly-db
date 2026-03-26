"""
Graph database updater for live dataset ingestion.

Provides functions to update the Neurobagel GraphDB instance with new JSON-LD data
without requiring container restart.
"""

import json
import logging
from pathlib import Path
from base64 import b64encode
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class GraphUpdater:
    """Updates GraphDB with new JSON-LD datasets without container restart."""

    def __init__(
        self,
        graph_host: str = "graph",
        graph_port: int = 7200,
        graph_db: str = "repositories/my_db",
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        """
        Initialize GraphUpdater.

        Parameters
        ----------
        graph_host : str
            GraphDB host (default: "graph" for devcontainer, "graphdb" for standalone Docker Compose)
        graph_port : int
            GraphDB HTTP port (default: 7200)
        graph_db : str
            GraphDB repository path (default: "repositories/my_db")
        username : str, optional
            GraphDB username (required for authenticated access)
        password : str, optional
            GraphDB password (required for authenticated access)
        """
        self.graph_host = graph_host
        self.graph_port = graph_port
        self.graph_db = graph_db
        self.username = username
        self.password = password
        self.base_url = f"http://{graph_host}:{graph_port}/{graph_db}"

    def _get_auth_header(self) -> Optional[dict]:
        """Build HTTP Basic Auth header if credentials are provided."""
        if self.username and self.password:
            credentials = b64encode(
                f"{self.username}:{self.password}".encode()).decode()
            return {"Authorization": f"Basic {credentials}"}
        return None

    def upload_jsonld(
        self,
        jsonld_path: Path,
        verbose: bool = True,
    ) -> bool:
        """
        Upload a JSON-LD file to the GraphDB instance.

        Parameters
        ----------
        jsonld_path : Path
            Path to the JSON-LD file to upload
        verbose : bool
            Whether to log detailed upload information

        Returns
        -------
        bool
            True if upload successful, False otherwise

        Raises
        ------
        FileNotFoundError
            If the JSON-LD file does not exist
        httpx.HTTPError
            If the HTTP request fails
        """
        if not jsonld_path.exists():
            raise FileNotFoundError(f"JSON-LD file not found: {jsonld_path}")

        with open(jsonld_path, "rb") as f:
            data = f.read()

        headers = self._get_auth_header() or {}
        headers["Content-Type"] = "application/ld+json"

        try:
            response = httpx.post(
                f"{self.base_url}/statements",
                content=data,
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()

            if verbose:
                logger.info(
                    f"✓ Successfully uploaded {jsonld_path.name} to GraphDB")
            return True

        except httpx.HTTPStatusError as e:
            logger.error(
                f"✗ Failed to upload {jsonld_path.name}: HTTP {e.response.status_code}\n"
                f"  Response: {e.response.text}"
            )
            return False
        except httpx.RequestError as e:
            logger.error(f"✗ Failed to connect to GraphDB: {e}")
            return False

    def update_datasets_metadata(
        self,
        datasets_metadata_path: Path,
        jsonld_path: Path,
        dataset_uuid: str,
        dataset_metadata: dict,
        verbose: bool = True,
    ) -> bool:
        """
        Update the datasets_metadata.json file with a new dataset's metadata.

        This file is read by the Neurobagel API to populate available datasets.

        Parameters
        ----------
        datasets_metadata_path : Path
            Path to datasets_metadata.json file
        jsonld_path : Path
            Path to the JSON-LD file being added
        dataset_uuid : str
            Unique identifier for the dataset
        dataset_metadata : dict
            Metadata dictionary with keys like 'dataset_name', 'authors', etc.
        verbose : bool
            Whether to log the update

        Returns
        -------
        bool
            True if metadata update successful, False otherwise
        """
        try:
            # Load existing metadata
            if datasets_metadata_path.exists():
                with open(datasets_metadata_path, "r") as f:
                    metadata = json.load(f)
            else:
                metadata = {}

            # Add or update the dataset
            metadata[dataset_uuid] = dataset_metadata

            # Write back
            with open(datasets_metadata_path, "w") as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)

            if verbose:
                logger.info(
                    f"✓ Updated datasets_metadata.json with {dataset_uuid}"
                )
            return True

        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"✗ Failed to update datasets_metadata.json: {e}")
            return False

    def hot_reload_dataset(
        self,
        jsonld_path: Path,
        dataset_uuid: str,
        dataset_metadata: dict,
        datasets_metadata_path: Optional[Path] = None,
    ) -> bool:
        """
        Hot-reload a dataset to GraphDB without container restart.

        This is the primary entry point for adding a new dataset to the running node.

        Parameters
        ----------
        jsonld_path : Path
            Path to the JSON-LD file to upload
        dataset_uuid : str
            Unique identifier for the dataset
        dataset_metadata : dict
            Metadata dictionary (e.g., from init_data processing)
        datasets_metadata_path : Path, optional
            Path to datasets_metadata.json. If None, update is skipped.
            Note: This file is typically inside the container's /data volume,
            which may not be accessible from the workspace. It's optional.

        Returns
        -------
        bool
            True if JSON-LD upload successful. Metadata update failure is non-blocking.

        Examples
        --------
        >>> updater = GraphUpdater(username="DBUSER", password="mypassword")
        >>> success = updater.hot_reload_dataset(
        ...     jsonld_path=Path("whole-spine.jsonld"),
        ...     dataset_uuid="my-dataset-001",
        ...     dataset_metadata={"dataset_name": "My Dataset", "authors": ["Jane Doe"]},
        ...     datasets_metadata_path=Path("/data/datasets_metadata.json"),
        ... )
        """
        logger.info(f"🚀 Hot-reloading dataset: {dataset_uuid}")
        logger.info(f"   JSON-LD source: {jsonld_path}")

        # Step 1: Upload JSON-LD to GraphDB (required)
        if not self.upload_jsonld(jsonld_path):
            logger.error("Hot-reload failed at JSON-LD upload step")
            return False

        # Step 2: Update datasets metadata (optional, non-blocking)
        if datasets_metadata_path:
            if not self.update_datasets_metadata(
                datasets_metadata_path, jsonld_path, dataset_uuid, dataset_metadata
            ):
                logger.warning(
                    "JSON-LD uploaded successfully, but metadata update failed. "
                    "Dataset is in GraphDB but may not appear in API results. "
                    "This can be fixed by manually placing the file at: "
                    f"{datasets_metadata_path}"
                )
                # Don't fail the entire operation - the data is safely in GraphDB
        else:
            logger.info(
                "ℹ️  Metadata update skipped (no --metadata-file provided). "
                "Dataset is in GraphDB but won't appear in API results until "
                "datasets_metadata.json is updated."
            )

        logger.info(f"✅ Hot-reload complete for {dataset_uuid}")
        logger.info(
            "   Graph updated without restart. Dataset should be queryable now.")
        return True
