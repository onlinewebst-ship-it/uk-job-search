"""Job-board clients.

Only boards with a documented, free, official API are supported. See
``docs/uk-market.md`` for why LinkedIn and Indeed are deliberately absent.
"""

from __future__ import annotations

from typing import Dict, Type

from ..errors import UsageError
from .adzuna import Adzuna
from .ats import ATS_NAMES, Ashby, Greenhouse, Lever
from .base import Source
from .reed import Reed

SOURCES: Dict[str, Type[Source]] = {
    "adzuna": Adzuna,
    "reed": Reed,
    "greenhouse": Greenhouse,
    "ashby": Ashby,
    "lever": Lever,
}
SOURCE_NAMES = tuple(SOURCES)


def get_source(name: str, **kwargs: object) -> Source:
    """Build a client by name, with a clear error for unknown sources."""
    key = str(name or "").strip().lower()
    if key not in SOURCES:
        raise UsageError(
            "Unknown source {!r}".format(name),
            hint="Supported sources: {}".format(", ".join(SOURCE_NAMES)),
        )
    return SOURCES[key](**kwargs)  # type: ignore[arg-type]


__all__ = [
    "Adzuna",
    "Ashby",
    "ATS_NAMES",
    "Greenhouse",
    "Lever",
    "Reed",
    "Source",
    "SOURCES",
    "SOURCE_NAMES",
    "get_source",
]
