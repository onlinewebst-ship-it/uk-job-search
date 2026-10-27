"""Shared plumbing for job-board clients."""

from __future__ import annotations

import os
from typing import List

from ..errors import MissingCredentialError
from ..models import Posting


def require_env(name: str, signup_url: str, purpose: str) -> str:
    """Read a required environment variable or fail with instructions.

    The error names the missing variable and where to get a key, because the
    most common first-run failure is a half-configured environment. Values are
    stripped: trailing whitespace pasted from a web page breaks HTTP Basic auth
    in a way that is very hard to diagnose.
    """
    value = os.environ.get(name, "").strip()
    if value:
        return value
    raise MissingCredentialError(
        "{} is not set; {} cannot run without it.".format(name, purpose),
        hint=(
            "Get a free key at {url}, then export it in the shell you run this "
            "from: export {name}='your-key'. See docs/getting-keys.md."
        ).format(url=signup_url, name=name),
        context={"env_var": name, "signup_url": signup_url},
    )


class Source:
    """Interface every job board client implements.

    ``search`` and ``detail`` are the only two operations the pipeline needs.
    Both return normalised :class:`~jobsearch.models.Posting` objects and raise
    :class:`~jobsearch.errors.JobSearchError` subclasses (never bare exceptions)
    for anything a user could act on.
    """

    name = "base"
    #: Where the user signs up for a free key, shown in error messages.
    signup_url = ""

    def search(
        self,
        query: str,
        location: str = "United Kingdom",
        limit: int = 25,
        **kwargs: object
    ) -> List[Posting]:  # pragma: no cover - interface
        raise NotImplementedError

    def detail(self, provider_id: str) -> Posting:  # pragma: no cover - interface
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<{} source>".format(self.name)


def coerce_limit(limit: int, maximum: int, source: str) -> int:
    """Validate a per-source result limit."""
    from ..errors import UsageError

    try:
        value = int(limit)
    except (TypeError, ValueError):
        raise UsageError("--limit must be an integer, got {!r}".format(limit))
    if value < 1:
        raise UsageError("--limit must be at least 1, got {}".format(value))
    return min(value, maximum)
