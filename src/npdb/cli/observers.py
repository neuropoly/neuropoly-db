from enum import Enum, auto
from threading import Lock
from typing import Any

from rich.progress import Progress

lock = Lock()


class MessageType(Enum):
    INFO = auto()
    WARNING = auto()
    ERROR = auto()


class UpdateType(Enum):
    PROGRESS = auto()
    MESSAGE = auto()


class Task:
    def __init__(
        self, id: Any, progress: Progress, description: str = "", *args, **kwargs
    ):
        self.id = id
        self.task_id = progress.add_task(description, *args, **kwargs)


class Observer:
    pass


class CLIDisplayObserver(Observer):
    pass


class CLIMessageObserver(CLIDisplayObserver):
    pass


class CLIProgressObserver(CLIDisplayObserver):
    def __init__(self, progress: Progress, color: str = "cyan"):
        self._tasks = []
        self._progress = progress
        self._color = color

    def update(self, description, task_id=None, *args, **kwargs):
        with lock:
            if task_id is None:
                try:
                    _task = next(filter(lambda t: t.id == description, self._tasks))
                    self._progress.update(
                        _task.task_id,
                        description=f"[{self._color}]{description}[/{self._color}]",
                        *args,
                        **kwargs,
                    )
                except StopIteration:
                    self._tasks.append(
                        Task(
                            description,
                            self._progress,
                            f"[{self._color}]{description}[/{self._color}]",
                            *args,
                            **kwargs,
                        )
                    )
            else:
                try:
                    _task = next(filter(lambda t: t.id == task_id, self._tasks))
                    self._progress.update(
                        _task.task_id,
                        description=f"[{self._color}]{description}[/{self._color}]",
                        *args,
                        **kwargs,
                    )
                except StopIteration:
                    self._tasks.append(
                        Task(
                            task_id,
                            self._progress,
                            f"[{self._color}]{description}[/{self._color}]",
                            *args,
                            **kwargs,
                        )
                    )

    def advance(self, description, task_id=None, advance=1, *args, **kwargs):
        with lock:
            if task_id is None:
                try:
                    _task = next(filter(lambda t: t.id == description, self._tasks))
                    self._progress.update(
                        _task.task_id,
                        description=f"[{self._color}]{description}[/{self._color}]",
                        advance=advance,
                        *args,
                        **kwargs,
                    )
                except StopIteration:
                    self._tasks.append(
                        Task(
                            description,
                            self._progress,
                            f"[{self._color}]{description}[/{self._color}]",
                            *args,
                            **kwargs,
                        )
                    )
            else:
                try:
                    _task = next(filter(lambda t: t.id == task_id, self._tasks))
                    self._progress.update(
                        _task.task_id,
                        description=f"[{self._color}]{description}[/{self._color}]",
                        advance=advance,
                        *args,
                        **kwargs,
                    )
                except StopIteration:
                    self._tasks.append(
                        Task(
                            task_id,
                            self._progress,
                            f"[{self._color}]{description}[/{self._color}]",
                            *args,
                            **kwargs,
                        )
                    )
