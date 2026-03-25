from abc import ABC, abstractmethod


class DBManager(ABC):
    def __init__(self):
        pass

    @property
    @abstractmethod
    def datasets(self):
        pass
