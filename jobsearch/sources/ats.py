"""Company careers-page (ATS) clients: Greenhouse, Ashby and Lever.

Why these exist
---------------
Adzuna and Reed are *job boards*: you send a query and they search an index of
adverts. Greenhouse, Ashby and Lever are *applicant tracking systems* - the JSON
that powers a company's own careers page. There is no query parameter and no API
key: you fetch a company's whole board and filter locally, which is why a search
against them needs a list of employers rather than keywords.

What that buys: the employer's own advert text, and the vacancy before an
aggregator picks it up. What it costs: you only see the companies you list, and
their boards are worldwide, so location filtering happens here rather than at
the provider.

Endpoints used::

    GET https://boards-api.greenhouse.io/v1/boards/{org}/jobs
    GET https://api.ashbyhq.com/posting-api/job-board/{org}
    GET https://api.lever.co/v0/postings/{org}?mode=json

None of the three requires authentication. They are published so that a company's
openings can be embedded on its own site; treat them politely (a handful of
requests per run, no polling loop).

Freshness caveat: Greenhouse's board endpoint exposes only ``updated_at``, which
is when the advert was last touched rather than when it was first posted. Ashby
publishes ``publishedAt`` and Lever ``createdAt``, which are true posting dates.
The stored ``extra["date_source"]`` records which one a posting used, so a
freshness decision can tell the difference.

Configuration
-------------
The default employer list lives in ``data/ats-boards.json``, keyed by provider
and mapping the board slug to a display name. Override the file with
``$UKJOBSEARCH_ATS_BOARDS``, or pass ``--org`` on the command line to search
specific companies.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..errors import DetailUnavailableError, NetworkError, SourceError, UsageError
from ..http import get_json
from ..models import Posting, stable_id
from ..textinfer import (
    contains_phrase,
    infer_remote,
    is_whole_uk,
    keyword_tokens,
    normalise_space,
    parse_date,
    plain_text,
)
from .base import Source, coerce_limit

MAX_BOARDS_PER_RUN = 40
MAX_RESULTS = 2000

# A posting counts as UK-reachable when its location text names the UK, a UK
# city, or a region that includes the UK. Remote roles are included only when
# the location says so as well: "Remote - United States" is not a UK job.
UK_HINTS: Tuple[str, ...] = (
    "united kingdom",
    "uk",
    "great britain",
    "england",
    "scotland",
    "wales",
    "northern ireland",
    "london",
    "manchester",
    "birmingham",
    "leeds",
    "glasgow",
    "edinburgh",
    "bristol",
    "cambridge",
    "oxford",
    "sheffield",
    "liverpool",
    "nottingham",
    "cardiff",
    "belfast",
    "newcastle",
    "reading",
    "milton keynes",
    "slough",
    "grimsby",
    "emea",
    "europe",
    "worldwide",
    "anywhere",
    "global",
)


def boards_path() -> Path:
    """Location of the employer list: ``$UKJOBSEARCH_ATS_BOARDS`` or the repo copy."""
    override = os.environ.get("UKJOBSEARCH_ATS_BOARDS", "").strip()
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[2] / "data" / "ats-boards.json"


def load_boards(provider: str, path: Optional[Path] = None) -> Dict[str, str]:
    """Return ``{slug: display name}`` for one provider.

    A missing or malformed file is an error rather than an empty board list,
    because an empty list would look like "this company has no vacancies".
    """
    target = path or boards_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise UsageError(
            "No ATS employer list at {}".format(target),
            hint=(
                "Create it with {} provider keys, pass --org, or set "
                "UKJOBSEARCH_ATS_BOARDS to an existing file."
            ).format(provider),
        )
    except ValueError as exc:
        raise UsageError(
            "{} is not valid JSON: {}".format(target, exc),
            hint="Fix the file, or pass --org to bypass it.",
        )
    section = raw.get(provider) if isinstance(raw, dict) else None
    if section is None:
        return {}
    if isinstance(section, list):
        # Tolerate the simple form: {"ashby": ["openai", "cohere"]}.
        return {str(slug).strip(): str(slug).strip() for slug in section if str(slug).strip()}
    if not isinstance(section, dict):
        raise UsageError(
            "{}: '{}' must be an object or a list".format(target, provider),
            hint='Use {"org-slug": "Display Name"}.',
        )
    return {
        str(slug).strip(): str(name).strip() or str(slug).strip()
        for slug, name in section.items()
        if str(slug).strip()
    }


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _location_text(value: Any) -> str:
    """Flatten the several shapes providers use for a location."""
    if isinstance(value, dict):
        for key in ("name", "locationName", "text"):
            if value.get(key):
                return normalise_space(str(value[key]))
        return normalise_space(", ".join(str(v) for v in value.values() if isinstance(v, str)))
    return normalise_space(str(value or ""))


def _epoch_ms_to_date(value: Any) -> Optional[str]:
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return None
    if millis <= 0:
        return None
    try:
        return _dt.datetime.fromtimestamp(millis / 1000.0, _dt.timezone.utc).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _contract_fields(value: Any) -> Tuple[str, str]:
    """Map an ATS employment-type string to ``(contract_type, contract_time)``."""
    text = str(value or "").lower().replace("-", "").replace("_", "").replace(" ", "")
    if not text:
        return "unknown", "unknown"
    if "part" in text:
        return "unknown", "part_time"
    if "contract" in text or "temporary" in text or "fixedterm" in text:
        return "contract", "unknown"
    if "full" in text or "permanent" in text:
        return "permanent", "full_time"
    return "unknown", "unknown"


class AtsSource(Source):
    """Shared behaviour for per-company board feeds."""

    provider = "ats"
    base_url = ""
    #: Documented by each provider; used in error messages.
    docs_url = ""

    def __init__(
        self,
        orgs: Optional[Sequence[str]] = None,
        boards: Optional[Dict[str, str]] = None,
        timeout: float = 20.0,
        path: Optional[Path] = None,
    ) -> None:
        self.timeout = timeout
        configured = boards if boards is not None else load_boards(self.provider, path)
        self.boards: Dict[str, str] = dict(configured)
        if orgs:
            # --org adds to the configured list rather than replacing it, so a
            # one-off search does not silently lose every other employer.
            for slug in orgs:
                cleaned = str(slug or "").strip()
                if cleaned:
                    self.boards.setdefault(cleaned, cleaned)

    # ------------------------------------------------------------------ board
    def board_url(self, slug: str) -> str:
        return self.base_url.format(org=slug)

    def parse_board(self, payload: Any, slug: str, name: str) -> List[Posting]:
        raise NotImplementedError  # pragma: no cover - interface

    # ----------------------------------------------------------------- search
    def search(
        self,
        query: str,
        location: str = "United Kingdom",
        limit: int = 25,
        orgs: Optional[Sequence[str]] = None,
        **kwargs: object
    ) -> List[Posting]:
        """Fetch every configured board, then filter locally.

        ``query`` is matched against title and description; every keyword word
        must appear. ``limit`` caps the merged result, newest first.
        """
        boards = dict(self.boards)
        if orgs:
            for slug in orgs:
                cleaned = str(slug or "").strip()
                if cleaned:
                    boards.setdefault(cleaned, cleaned)
        if not boards:
            raise UsageError(
                "No {} boards configured".format(self.provider),
                hint=(
                    "Add employer slugs to data/ats-boards.json, pass --org, or "
                    "set UKJOBSEARCH_ATS_BOARDS to a file."
                ),
            )
        if len(boards) > MAX_BOARDS_PER_RUN:
            raise UsageError(
                "{} boards configured, the limit is {}".format(
                    len(boards), MAX_BOARDS_PER_RUN
                ),
                hint="Trim data/ats-boards.json; one request is made per board.",
            )
        limit = coerce_limit(limit, MAX_RESULTS, self.provider)
        tokens = keyword_tokens(query)

        postings: List[Posting] = []
        failures: List[str] = []
        for slug, name in sorted(boards.items()):
            try:
                payload = get_json(self.board_url(slug), timeout=self.timeout)
            except NetworkError as exc:
                # Collected rather than raised immediately so one stale slug
                # does not hide the rest, and the message names every bad slug.
                failures.append("{} ({})".format(slug, exc.message))
                continue
            postings.extend(self.parse_board(payload, slug, name))

        if failures:
            raise SourceError(
                "{} of {} {} boards could not be read: {}".format(
                    len(failures), len(boards), self.provider, "; ".join(failures)
                ),
                hint=(
                    "Check the board slug (the company's careers-page identifier) "
                    "and remove or correct it in data/ats-boards.json."
                ),
                context={"provider": self.provider, "failed": len(failures)},
            )

        matched = [
            posting
            for posting in postings
            if self._matches_query(posting, tokens)
            and self._in_location(posting, location)
        ]
        matched.sort(key=lambda p: (p.posted_date or "", p.title.lower()), reverse=True)
        return matched[:limit]

    # ----------------------------------------------------------------- detail
    def detail(self, provider_id: str) -> Posting:
        raise DetailUnavailableError(
            "{} has no single-advert endpoint in this tool; the search response "
            "already carries the full description.".format(self.provider),
            hint="Read the posting with 'detail <id>', or open its URL.",
            context={"source": self.provider, "provider_id": provider_id},
        )

    # --------------------------------------------------------------- filtering
    @staticmethod
    def _matches_query(posting: Posting, tokens: Sequence[str]) -> bool:
        if not tokens:
            return True
        haystack = "{} {}".format(posting.title or "", posting.description or "")
        return all(contains_phrase(haystack, token) for token in tokens)

    @staticmethod
    def _in_location(posting: Posting, location: str) -> bool:
        text = posting.location or ""
        if not text:
            # A board with no location at all: keep it, the description decides.
            return True
        if is_whole_uk(location) or not str(location or "").strip():
            return any(contains_phrase(text, hint) for hint in UK_HINTS)
        if contains_phrase(text, location):
            return True
        # "Remote" alone is not enough: a US-remote role is not a UK role.
        return posting.is_remote and any(
            contains_phrase(text, hint) for hint in ("worldwide", "anywhere", "global", "emea", "europe")
        )


class Greenhouse(AtsSource):
    """Greenhouse job boards (``boards-api.greenhouse.io``)."""

    provider = "greenhouse"
    name = "greenhouse"
    base_url = "https://boards-api.greenhouse.io/v1/boards/{org}/jobs"
    docs_url = "https://developers.greenhouse.io/job-board.html"

    def parse_board(self, payload: Any, slug: str, name: str) -> List[Posting]:
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        if jobs is None:
            raise SourceError(
                "Greenhouse board {!r} returned no 'jobs' field".format(slug),
                hint="The endpoint may have changed; see {}.".format(self.docs_url),
            )
        if not isinstance(jobs, list):
            raise SourceError(
                "Greenhouse board {!r} returned 'jobs' as {}".format(
                    slug, type(jobs).__name__
                )
            )
        out: List[Posting] = []
        for item in jobs:
            if not isinstance(item, dict):
                continue
            job_id = str(item.get("id") or "").strip()
            title = normalise_space(item.get("title"))
            if not job_id or not title:
                continue
            location = _location_text(item.get("location"))
            description = plain_text(str(item.get("content") or ""))
            posted = parse_date(item.get("updated_at") or item.get("first_published"))
            out.append(
                Posting(
                    id=stable_id(self.name, "{}:{}".format(slug, job_id)),
                    source=self.name,
                    provider_id="{}:{}".format(slug, job_id),
                    title=title,
                    company=name,
                    location=location,
                    url=str(item.get("absolute_url") or ""),
                    description=description,
                    posted_date=posted,
                    is_remote=infer_remote(title, location, description),
                    extra={
                        "org": slug,
                        "provider": self.provider,
                        # Greenhouse exposes only updated_at on the board
                        # endpoint, so freshness here means "last touched",
                        # which can be much newer than first posting.
                        "date_source": "updated_at",
                    },
                )
            )
        return out


class Ashby(AtsSource):
    """Ashby job boards (``api.ashbyhq.com``)."""

    provider = "ashby"
    name = "ashby"
    base_url = "https://api.ashbyhq.com/posting-api/job-board/{org}"
    docs_url = "https://developers.ashbyhq.com/docs/public-job-posting-api"

    def parse_board(self, payload: Any, slug: str, name: str) -> List[Posting]:
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        if jobs is None:
            raise SourceError(
                "Ashby board {!r} returned no 'jobs' field".format(slug),
                hint="The endpoint may have changed; see {}.".format(self.docs_url),
            )
        if not isinstance(jobs, list):
            raise SourceError(
                "Ashby board {!r} returned 'jobs' as {}".format(
                    slug, type(jobs).__name__
                )
            )
        out: List[Posting] = []
        for item in jobs:
            if not isinstance(item, dict):
                continue
            job_id = str(item.get("id") or "").strip()
            title = normalise_space(item.get("title"))
            if not job_id or not title:
                continue
            location = _location_text(item.get("location"))
            description = plain_text(
                str(item.get("descriptionHtml") or item.get("descriptionPlain") or "")
            )
            contract_type, contract_time = _contract_fields(item.get("employmentType"))
            is_remote = bool(item.get("isRemote")) or infer_remote(
                title, location, description
            )
            out.append(
                Posting(
                    id=stable_id(self.name, "{}:{}".format(slug, job_id)),
                    source=self.name,
                    provider_id="{}:{}".format(slug, job_id),
                    title=title,
                    company=name,
                    location=location,
                    contract_type=contract_type,
                    contract_time=contract_time,
                    url=str(item.get("jobUrl") or ""),
                    description=description,
                    posted_date=parse_date(item.get("publishedAt")),
                    is_remote=is_remote,
                    extra={
                        "org": slug,
                        "provider": self.provider,
                        "department": str(item.get("department") or ""),
                        "team": str(item.get("team") or ""),
                        "employment_type": str(item.get("employmentType") or ""),
                    },
                )
            )
        return out


class Lever(AtsSource):
    """Lever job boards (``api.lever.co``)."""

    provider = "lever"
    name = "lever"
    base_url = "https://api.lever.co/v0/postings/{org}?mode=json"
    docs_url = "https://github.com/lever/postings-api"

    def parse_board(self, payload: Any, slug: str, name: str) -> List[Posting]:
        jobs = payload if isinstance(payload, list) else None
        if jobs is None:
            raise SourceError(
                "Lever board {!r} did not return a list of postings".format(slug),
                hint="The endpoint may have changed; see {}.".format(self.docs_url),
            )
        out: List[Posting] = []
        for item in jobs:
            if not isinstance(item, dict):
                continue
            job_id = str(item.get("id") or "").strip()
            title = normalise_space(item.get("text"))
            if not job_id or not title:
                continue
            categories = item.get("categories") if isinstance(item.get("categories"), dict) else {}
            location = _location_text(categories.get("location"))
            all_locations = ", ".join(
                _location_text(value) for value in _as_list(categories.get("allLocations"))
            )
            if all_locations and all_locations != location:
                location = normalise_space("{}{}".format(
                    location, (", " + all_locations) if location else all_locations
                ))
            description = plain_text(
                str(item.get("descriptionPlain") or item.get("description") or "")
            )
            contract_type, contract_time = _contract_fields(categories.get("commitment"))
            workplace = str(item.get("workplaceType") or "").lower()
            is_remote = workplace == "remote" or infer_remote(title, location, description)
            out.append(
                Posting(
                    id=stable_id(self.name, "{}:{}".format(slug, job_id)),
                    source=self.name,
                    provider_id="{}:{}".format(slug, job_id),
                    title=title,
                    company=name,
                    location=location,
                    contract_type=contract_type,
                    contract_time=contract_time,
                    url=str(item.get("hostedUrl") or ""),
                    description=description,
                    posted_date=_epoch_ms_to_date(item.get("createdAt")),
                    is_remote=is_remote,
                    extra={
                        "org": slug,
                        "provider": self.provider,
                        "team": str(categories.get("team") or ""),
                        "commitment": str(categories.get("commitment") or ""),
                        "workplace_type": workplace,
                    },
                )
            )
        return out


ATS_SOURCES = (Greenhouse, Ashby, Lever)
ATS_NAMES = tuple(source.name for source in ATS_SOURCES)
