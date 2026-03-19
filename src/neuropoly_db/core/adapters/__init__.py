from abc import ABC, abstractmethod


class BaseAdapter(ABC):
    @abstractmethod
    @property
    def template():
        pass

    