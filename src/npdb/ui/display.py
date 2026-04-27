"""
Live display for npdb CLI commands (docker-buildx style).

Usage::

    from rich.live import Live
    from npdb.ui.display import CommandDisplay

    display = CommandDisplay()
    with Live(display, refresh_per_second=4, transient=False) as live:
        display.start_step("Cloning repository")
        # ... do work ...
        display.complete_step()

        display.start_step("Running annotation")
        # ... do work, stream output ...
        display.append_output("some line of output")
        display.complete_step()
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Generator, List

from rich.console import Group
from rich.panel import Panel
from rich.text import Text


class StepStatus(Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"


@dataclass
class Step:
    """A single tracked step in a :class:`CommandDisplay`."""

    name: str
    status: StepStatus = StepStatus.RUNNING
    output: List[str] = field(default_factory=list)

    def __rich__(self):
        if self.status == StepStatus.SUCCESS:
            return Text(f"\u2705 {self.name}", style="green")
        if self.status == StepStatus.RUNNING:
            body = "\n".join(
                self.output) if self.output else "[dim]Running\u2026[/dim]"
            return Panel(
                body,
                title=f"[yellow]\u27f3 {self.name}[/yellow]",
                border_style="yellow",
            )
        # FAILURE
        body = "\n".join(self.output) if self.output else ""
        return Panel(
            body,
            title=f"[red]\u274c {self.name}[/red]",
            border_style="red",
        )


class CommandDisplay:
    """
    Maintains an ordered list of :class:`Step` objects and renders them as a
    Rich *Group* suitable for use with :class:`rich.live.Live`.

    Typical workflow per step::

        display.start_step("Step name")
        display.append_output("line of output")   # optional, repeatable
        display.complete_step()                    # or display.fail_step()
    """

    def __init__(self) -> None:
        self._steps: List[Step] = []

    # ------------------------------------------------------------------
    # Rich protocol
    # ------------------------------------------------------------------

    def __rich__(self):
        return Group(*self._steps)

    # ------------------------------------------------------------------
    # Step lifecycle
    # ------------------------------------------------------------------

    def start_step(self, name: str) -> None:
        """Append a new step in RUNNING state."""
        self._steps.append(Step(name=name))

    def append_output(self, line: str) -> None:
        """Append an output line to the currently running step."""
        if self._steps:
            self._steps[-1].output.append(line)

    def complete_step(self) -> None:
        """Mark the last step as SUCCESS."""
        if self._steps:
            self._steps[-1].status = StepStatus.SUCCESS

    def fail_step(self, output: List[str] | None = None) -> None:
        """Mark the last step as FAILURE, optionally replacing its output."""
        if self._steps:
            self._steps[-1].status = StepStatus.FAILURE
            if output is not None:
                self._steps[-1].output = list(output)


class _LineCallbackWriter:
    """A stdout-compatible writer that feeds complete lines to a callback."""

    encoding = "utf-8"
    errors = "replace"

    def __init__(self, callback: Callable[[str], None]) -> None:
        self._callback = callback
        self._buf = ""

    def write(self, text: str) -> int:
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._callback(line)
        return len(text)

    def flush(self) -> None:
        pass

    def fileno(self) -> int:  # needed by some libraries that call fileno()
        raise OSError("_LineCallbackWriter has no real file descriptor")


@contextmanager
def capture_stdout(callback: Callable[[str], None]) -> Generator[None, None, None]:
    """Context manager that redirects sys.stdout lines to *callback*.

    Rich's ``Live`` console is unaffected because it holds its own file
    reference captured at construction time.
    """
    original = sys.stdout
    sys.stdout = _LineCallbackWriter(callback)  # type: ignore[assignment]
    try:
        yield
    finally:
        sys.stdout = original
