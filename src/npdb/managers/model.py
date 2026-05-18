import os
from abc import ABC, abstractmethod
from typing import Any, List


class Manager(ABC):
    def __init__(self):
        pass

    @property
    @abstractmethod
    def datasets(self) -> Any:
        pass


class BagelDB:
    def __init__(self, jsonld_root: str):
        self.root = jsonld_root


class NeurobagelManager(Manager):
    def __init__(self, jsonld: str):
        self.db = BagelDB(jsonld)

    @property
    def datasets(self) -> List[str]:
        return os.listdir(self.db.root)

    def load_dataset(self, dataset: str, destination_path: str, light: bool = False):
        """Stub — implemented by subclasses (e.g. BagelNeuroPolyMTL)."""

    def extend_description(
        self, dataset: str, dataset_path: str, extra_keywords: list[str] = []
    ) -> dict:
        """Stub — implemented by subclasses (e.g. BagelNeuroPolyMTL)."""
        return {}
