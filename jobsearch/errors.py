"""Error types.

Every failure the CLI can hit is an instance of :class:`JobSearchError`, which
carries a machine-readable ``code``, a human message and an optional ``hint``
telling the user how to fix it. The CLI prints these as JSON on stderr and
exits 1, so callers never see a traceback for an expected problem (missing key,
rate limit, bad flag, corrupt state file).
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class JobSearchError(Exception):
    """Base class for all expected failures."""

    code = "error"
    exit_code = 1

    def __init__(
        self,
        message: str,
        hint: Optional[str] = None,
        code: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        if code:
            self.code = code
        self.context = dict(context or {})

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"code": self.code, "message": self.message}
        if self.hint:
            payload["hint"] = self.hint
        if self.context:
            payload["context"] = self.context
        return payload

    def __str__(self) -> str:
        if self.hint:
            return "{} ({})".format(self.message, self.hint)
        return self.message


class UsageError(JobSearchError):
    """Bad command line usage: unknown flag, bad value, missing argument."""

    code = "usage"


class MissingCredentialError(JobSearchError):
    """A required API key environment variable is not set."""

    code = "missing_credential"


class NetworkError(JobSearchError):
    """The provider could not be reached, or returned an unusable response."""

    code = "network"


class SourceError(JobSearchError):
    """The provider answered, but rejected the request or sent unexpected data."""

    code = "source_error"


class StateError(JobSearchError):
    """The seen-postings store is unreadable or would be corrupted."""

    code = "state_error"


class NotFoundError(JobSearchError):
    """No stored posting matches the given id."""

    code = "not_found"


class DetailUnavailableError(JobSearchError):
    """The source has no endpoint that returns a single advert in full."""

    code = "detail_unavailable"


class AmbiguousIdError(JobSearchError):
    """A posting id prefix matches more than one stored posting."""

    code = "ambiguous_id"
