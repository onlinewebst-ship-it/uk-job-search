"""Adzuna API v1 client (UK by default, other countries supported).

Docs: https://developer.adzuna.com/docs/search
Keys: https://developer.adzuna.com/signup  (free tier, no card)

Endpoint used::

    GET https://api.adzuna.com/v1/api/jobs/{country}/search/{page}
        ?app_id=...&app_key=...&what=...&where=...
        &results_per_page=...&max_days_old=...&salary_min=...&category=...
        &content-type=application/json

Adzuna's public v1 API has no documented endpoint that returns a single advert
by id, and search results carry only a truncated description. ``detail`` is
therefore honest about that: it returns a stored snapshot when one is supplied
and otherwise raises :class:`DetailUnavailableError` telling the caller to use
the advert URL. Reed has a real detail endpoint; Adzuna does not.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..errors import DetailUnavailableError, SourceError, UsageError
from ..http import get_json
from ..models import Posting, stable_id
from ..textinfer import infer_remote, is_whole_uk, parse_date
from .base import Source, coerce_limit, require_env

BASE_URL = "https://api.adzuna.com/v1/api/jobs"
SIGNUP_URL = "https://developer.adzuna.com/signup"
MAX_PER_PAGE = 50
MAX_PAGES = 20
SUPPORTED_COUNTRIES = (
    "gb",
    "us",
    "at",
    "au",
    "be",
    "br",
    "ca",
    "ch",
    "de",
    "es",
    "fr",
    "in",
    "it",
    "mx",
    "nl",
    "nz",
    "pl",
    "sg",
    "za",
)
_COUNTRY_CURRENCY = {
    "gb": "GBP",
    "us": "USD",
    "au": "AUD",
    "ca": "CAD",
    "nz": "NZD",
    "in": "INR",
    "sg": "SGD",
    "za": "ZAR",
    "br": "BRL",
    "ch": "CHF",
    "pl": "PLN",
    "mx": "MXN",
}


class Adzuna(Source):
    """Client for the Adzuna job search API."""

    name = "adzuna"
    signup_url = SIGNUP_URL

    def __init__(self, country: str = "gb", timeout: float = 20.0) -> None:
        country = (country or "gb").strip().lower()
        if country not in SUPPORTED_COUNTRIES:
            raise UsageError(
                "Unsupported Adzuna country {!r}".format(country),
                hint="Choose one of: {}".format(", ".join(SUPPORTED_COUNTRIES)),
            )
        self.country = country
        self.timeout = timeout

    # ------------------------------------------------------------------ keys
    def _credentials(self) -> Dict[str, str]:
        """Return both Adzuna credentials or raise naming the missing one(s)."""
        app_id = require_env(
            "ADZUNA_APP_ID", SIGNUP_URL, "the Adzuna source"
        )
        app_key = require_env(
            "ADZUNA_APP_KEY", SIGNUP_URL, "the Adzuna source"
        )
        return {"app_id": app_id, "app_key": app_key}

    # ---------------------------------------------------------------- search
    def search(
        self,
        query: str,
        location: str = "United Kingdom",
        limit: int = 25,
        max_days_old: Optional[int] = None,
        salary_min: Optional[int] = None,
        category: Optional[str] = None,
        page: int = 1,
    ) -> List[Posting]:
        """Search Adzuna and return normalised postings.

        Parameters mirror the provider's query string. ``location`` maps to
        Adzuna's ``where``; an empty location searches the whole country.
        ``max_days_old`` maps to Adzuna's freshness filter (0 means no limit
        on the provider side).
        """
        if not str(query or "").strip():
            raise UsageError("--query must not be empty for an Adzuna search")
        limit = coerce_limit(limit, MAX_PER_PAGE * 20, "adzuna")
        credentials = self._credentials()

        postings: List[Posting] = []
        current_page = max(1, int(page))
        while len(postings) < limit:
            per_page = min(MAX_PER_PAGE, limit - len(postings))
            params: Dict[str, Any] = {
                "app_id": credentials["app_id"],
                "app_key": credentials["app_key"],
                "what": query,
                # "United Kingdom" means the whole country: omit `where` so
                # Adzuna does not try to geocode a country name.
                "where": None if is_whole_uk(location) else (location or "").strip(),
                "results_per_page": per_page,
                "max_days_old": max_days_old,
                "salary_min": salary_min,
                "category": category,
            }
            payload = get_json(
                "{}/{}/search/{}".format(BASE_URL, self.country, current_page),
                params=params,
                timeout=self.timeout,
            )
            page_postings = self.parse_search(payload)
            postings.extend(page_postings)
            if len(page_postings) < per_page:
                break
            current_page += 1
            if current_page > MAX_PAGES:
                # Safety valve: Adzuna stops serving deep pages anyway, and an
                # unhelpful API that always returns a full page must not loop.
                break

        return postings[:limit]

    def detail(self, provider_id: str, cached: Optional[Posting] = None) -> Posting:
        """Return the stored snapshot, or explain why Adzuna cannot do better.

        Adzuna truncates ``description`` in search responses and exposes no
        single-advert endpoint, so a live refresh is impossible. Callers that
        already hold a posting (the CLI's ``detail`` command reads the local
        store) pass it as ``cached``.
        """
        if cached is not None and (cached.description or "").strip():
            return cached
        raise DetailUnavailableError(
            "Adzuna's API does not provide a full advert for id {!r}; search "
            "responses contain only a truncated description.".format(provider_id),
            hint=(
                "Open the stored advert URL to read the full description. The "
                "CLI stores the snippet from the search that found it, so "
                "'python3 -m jobsearch detail <id>' works without a refresh."
            ),
            context={"source": "adzuna", "provider_id": str(provider_id)},
        )

    # --------------------------------------------------------------- parsing
    def parse_search(self, payload: Any) -> List[Posting]:
        """Map an Adzuna search response into postings."""
        if not isinstance(payload, dict):
            raise SourceError(
                "Adzuna returned {} instead of a JSON object".format(
                    type(payload).__name__
                ),
                hint="The API may have changed. Compare with developer.adzuna.com/docs.",
            )
        results = payload.get("results")
        if results is None:
            raise SourceError(
                "Adzuna response has no 'results' field",
                hint="The API may have changed. Compare with developer.adzuna.com/docs.",
            )
        if not isinstance(results, list):
            raise SourceError("Adzuna 'results' was not a list")
        postings: List[Posting] = []
        for item in results:
            posting = self.parse_posting(item)
            if posting is not None:
                postings.append(posting)
        return postings

    def parse_posting(self, item: Any) -> Optional[Posting]:
        """Map one Adzuna advert; returns ``None`` when the advert is unusable."""
        if not isinstance(item, dict):
            return None
        provider_id = str(item.get("id") or "").strip()
        title = " ".join(str(item.get("title") or "").split())
        if not provider_id or not title:
            return None

        company_block = item.get("company") or {}
        company = ""
        if isinstance(company_block, dict):
            company = str(company_block.get("display_name") or "").strip()

        location_block = item.get("location") or {}
        location = ""
        if isinstance(location_block, dict):
            location = str(location_block.get("display_name") or "").strip()

        description = " ".join(str(item.get("description") or "").split())
        contract_type = _normalise_contract_type(item.get("contract_type"))
        contract_time = _normalise_contract_time(item.get("contract_time"))
        posted_date = parse_date(item.get("created"))
        currency = _COUNTRY_CURRENCY.get(self.country, "GBP")
        url = str(item.get("redirect_url") or "").strip()

        return Posting(
            id=stable_id(self.name, provider_id),
            source=self.name,
            provider_id=provider_id,
            title=title,
            company=company,
            location=location,
            salary_min=item.get("salary_min"),
            salary_max=item.get("salary_max"),
            currency=currency,
            contract_type=contract_type,
            contract_time=contract_time,
            posted_date=posted_date,
            url=url,
            description=description,
            is_remote=infer_remote(title, location, description),
            salary_is_predicted=_as_bool(item.get("salary_is_predicted")),
            extra={
                "category": _category_label(item.get("category")),
                "adref": str(item.get("adref") or ""),
            },
        )


def _normalise_contract_type(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in ("permanent", "perm"):
        return "permanent"
    if text in ("contract", "contractor", "freelance", "self_employed"):
        return "contract"
    if text in ("temporary", "temp", "seasonal"):
        return "temporary"
    return "unknown"


def _normalise_contract_time(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in ("part_time", "parttime"):
        return "part_time"
    if text in ("full_time", "fulltime"):
        return "full_time"
    return "unknown"


def _as_bool(value: Any) -> bool:
    """Adzuna sends 0/1, sometimes as strings; "0" must not read as truthy."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "y")


def _category_label(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("label") or value.get("tag") or "")
    if isinstance(value, str):
        return value
    return ""
