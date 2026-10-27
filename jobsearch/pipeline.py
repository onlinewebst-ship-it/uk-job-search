"""Search orchestration: run clients, merge, de-duplicate.

Two kinds of duplicate are removed:

1. **Same posting twice** - identical ``Posting.id`` (same board, same advert),
   possible when several pages overlap.
2. **Same job on two boards** - identical (title, company, location) after
   whitespace/case normalisation. The first source in ``--source`` order wins,
   so ``--source reed --source adzuna`` prefers Reed's full description.

The identity key requires a company name: adverts that hide the employer
(common for agency listings) are only merged when their ids match, because
"Data Analyst" at "Not stated" in "London" is not evidence of the same job.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .errors import UsageError
from .models import Posting
from .sources.base import Source


@dataclass
class SearchRequest:
    """Arguments shared by every source, mapped per provider in one place."""

    query: str
    location: str = "United Kingdom"
    limit: int = 25
    max_days_old: Optional[int] = None
    salary_min: Optional[int] = None
    category: Optional[str] = None
    distance: Optional[int] = None


def search_source(client: Source, request: SearchRequest) -> List[Posting]:
    """Call one client with the parameters it actually supports."""
    if client.name == "adzuna":
        return client.search(  # type: ignore[call-arg]
            request.query,
            request.location,
            limit=request.limit,
            max_days_old=request.max_days_old,
            salary_min=request.salary_min,
            category=request.category,
        )
    if client.name == "reed":
        return client.search(  # type: ignore[call-arg]
            request.query,
            request.location,
            limit=request.limit,
            distance=request.distance,
            salary_min=request.salary_min,
        )
    raise UsageError("No search mapping for source {!r}".format(client.name))


def search_all(
    clients: Sequence[Source], request: SearchRequest
) -> Tuple[List[Posting], Dict[str, int]]:
    """Run every client and return merged postings plus per-source counts.

    A failing source aborts the whole run rather than returning a partial
    result that looks complete: silently missing half the market is worse than
    an error the user can act on.
    """
    collected: List[Posting] = []
    per_source: Dict[str, int] = {}
    for client in clients:
        found = search_source(client, request)
        per_source[client.name] = len(found)
        collected.extend(found)
    return dedupe(collected), per_source


def dedupe(postings: Sequence[Posting]) -> List[Posting]:
    """Remove duplicate ids and cross-board duplicates, preserving order."""
    seen_ids = set()
    identities = set()
    out: List[Posting] = []
    for posting in postings:
        if posting.id in seen_ids:
            continue
        seen_ids.add(posting.id)
        key = identity_key(posting)
        if key is not None:
            if key in identities:
                continue
            identities.add(key)
        out.append(posting)
    return out


def identity_key(posting: Posting) -> Optional[Tuple[str, str, str]]:
    """Cross-board identity, or ``None`` when it would be unsafe to merge."""
    title = _norm(posting.title)
    company = _norm(posting.company)
    if not title or not company:
        return None
    if company.lower() in ("not stated", "unknown", "confidential", "private"):
        return None
    return title, company, _norm(posting.location)


def _norm(value: str) -> str:
    return " ".join(str(value or "").lower().split())
