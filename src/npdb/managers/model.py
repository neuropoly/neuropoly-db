from abc import ABC, abstractmethod


class Manager(ABC):
    def __init__(self):
        pass

    @property
    @abstractmethod
    def datasets(self):
        pass
