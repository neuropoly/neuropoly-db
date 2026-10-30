"""Errors shared by the library and standalone command."""


class BidsRepairError(RuntimeError):
    """A repair plan could not be safely applied or recovered."""
