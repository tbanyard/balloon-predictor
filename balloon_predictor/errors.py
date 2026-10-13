class PredictorError(Exception):
    """An actionable error that is safe to display to a user."""


class CoverageError(PredictorError):
    """The flight has left the available weather coverage."""
