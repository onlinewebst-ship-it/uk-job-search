"""Reed Jobseeker API client.

Docs: https://www.reed.co.uk/developers/jobseeker
Keys: https://www.reed.co.uk/developers/jobseeker  (free, register then copy
the API key from "My API key")

Authentication is HTTP Basic with the API key as the *username* and an empty
password, i.e. ``Authorization: Basic base64("<key>:")``. Reed returns 401 if
the trailing colon is omitted, which is the usual cause of "my key does not
work" reports.

Endpoints used::

    GET https://www.reed.co.uk/api/1.0/search?keywords=...
    GET https://www.reed.co.uk/api/1.0/jobs/{jobId}

``search`` returns a truncated description and no contract fields in practice;
``detail`` returns the full advert, including ``contractType``, ``jobType`` and
salary type.
"""

from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional

from ..errors import DetailUnavailableError, SourceError, UsageError
from ..http import get_json
from ..models import Posting, stable_id
from ..textinfer import infer_remote, is_whole_uk, parse_date
from .base import Source, coerce_limit, require_env

BASE_URL = "https://www.reed.co.uk/api/1.0"
SIGNUP_URL = "https://www.reed.co.uk/developers/jobseeker"
MAX_RESULTS = 100


class Reed(Source):
    """Client for the Reed Jobseeker API."""

    name = "reed"
    signup_url = SIGNUP_URL

    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout

    # ------------------------------------------------------------------ auth
    def _api_key(self) -> str:
        return require_env("REED_API_KEY", SIGNUP_URL, "the Reed source")

    def _auth_headers(self) -> Dict[str, str]:
        key = self._api_key()
        token = base64.b64encode("{}:".format(key).encode("utf-8")).decode("ascii")
        return {"Authorization": "Basic {}".format(token)}

    # ---------------------------------------------------------------- search
    def search(
        self,
        query: str,
        location: str = "United Kingdom",
        limit: int = 25,
        distance: Optional[int] = None,
        salary_min: Optional[int] = None,
        salary_max: Optional[int] = None,
        permanent: Optional[bool] = None,
        contract: Optional[bool] = None,
        temp: Optional[bool] = None,
        part_time: Optional[bool] = None,
        full_time: Optional[bool] = None,
        agency_only: Optional[bool] = None,
        skip: int = 0,
    ) -> List[Posting]:
        """Search Reed and return normalised postings.

        ``limit`` is capped at 100 by the provider; larger values are paged by
        calling this method with ``skip`` yourself. Boolean parameters are only
        sent when explicitly set, because Reed treats an explicit ``false`` as
        a filter rather than "no preference".
        """
        if not str(query or "").strip():
            raise UsageError("--query must not be empty for a Reed search")
        limit = coerce_limit(limit, MAX_RESULTS, "reed")
        whole_uk = is_whole_uk(location)
        if distance is not None and whole_uk:
            raise UsageError(
                "--distance needs a place to measure from; add --location "
                "(for example --location Manchester)."
            )
        params: Dict[str, Any] = {
            "keywords": query,
            # Reed is UK-only, so a whole-UK location means "no filter" rather
            # than locationName="United Kingdom", which its geocoder rejects.
            "locationName": None if whole_uk else ((location or "").strip() or None),
            "distanceFromLocation": distance,
            "minimumSalary": salary_min,
            "maximumSalary": salary_max,
            "permanent": _bool_param(permanent),
            "contract": _bool_param(contract),
            "temp": _bool_param(temp),
            "partTime": _bool_param(part_time),
            "fullTime": _bool_param(full_time),
            "postedByRecruitmentAgency": _bool_param(agency_only),
            "resultsToTake": limit,
            "resultsToSkip": max(0, int(skip)) or None,
        }
        payload = get_json(
            "{}/search".format(BASE_URL),
            params=params,
            headers=self._auth_headers(),
            timeout=self.timeout,
        )
        return self.parse_search(payload)[:limit]

    # ---------------------------------------------------------------- detail
    def detail(self, provider_id: str) -> Posting:
        """Fetch one advert in full by Reed job id."""
        job_id = str(provider_id or "").strip()
        if not job_id:
            raise UsageError("Reed detail requires a job id")
        payload = get_json(
            "{}/jobs/{}".format(BASE_URL, job_id),
            headers=self._auth_headers(),
            timeout=self.timeout,
        )
        posting = self.parse_detail(payload, job_id)
        if posting is None:
            raise DetailUnavailableError(
                "Reed returned no advert for job id {!r}".format(job_id),
                hint=(
                    "The advert has probably expired. Reed keeps listings for "
                    "around 30 days after the closing date."
                ),
                context={"source": "reed", "provider_id": job_id},
            )
        return posting

    # --------------------------------------------------------------- parsing
    def parse_search(self, payload: Any) -> List[Posting]:
        """Map a Reed search response into postings."""
        if isinstance(payload, list):
            results = payload
        elif isinstance(payload, dict):
            results = payload.get("results")
        else:
            raise SourceError(
                "Reed returned {} instead of a JSON object".format(
                    type(payload).__name__
                )
            )
        if results is None:
            raise SourceError(
                "Reed response has no 'results' field",
                hint="The API may have changed. Compare with reed.co.uk/developers.",
            )
        if not isinstance(results, list):
            raise SourceError("Reed 'results' was not a list")
        postings: List[Posting] = []
        for item in results:
            posting = self.parse_posting(item)
            if posting is not None:
                postings.append(posting)
        return postings

    def parse_detail(self, payload: Any, job_id: str = "") -> Optional[Posting]:
        """Map a Reed single-job response (or an empty object) to a posting."""
        if not isinstance(payload, dict) or not payload:
            return None
        if not payload.get("jobId") and not payload.get("jobTitle"):
            return None
        return self.parse_posting(payload, fallback_id=job_id)

    def parse_posting(self, item: Any, fallback_id: str = "") -> Optional[Posting]:
        """Map one Reed advert; returns ``None`` when the advert is unusable."""
        if not isinstance(item, dict):
            return None
        provider_id = str(item.get("jobId") or fallback_id or "").strip()
        title = " ".join(str(item.get("jobTitle") or "").split())
        if not provider_id or not title:
            return None

        description = str(item.get("jobDescription") or "")
        description_clean = " ".join(description.split())
        location = " ".join(str(item.get("locationName") or "").split())
        company = " ".join(str(item.get("employerName") or "").split())
        url = str(item.get("jobUrl") or "").strip()
        if not url:
            url = "https://www.reed.co.uk/jobs/{}".format(provider_id)

        contract_type = _normalise_contract_type(item.get("contractType"))
        contract_time = _normalise_contract_time(item)

        posting = Posting(
            id=stable_id(self.name, provider_id),
            source=self.name,
            provider_id=provider_id,
            title=title,
            company=company,
            location=location,
            salary_min=item.get("minimumSalary"),
            salary_max=item.get("maximumSalary"),
            currency=str(item.get("currency") or "GBP").strip().upper() or "GBP",
            contract_type=contract_type,
            contract_time=contract_time,
            posted_date=parse_date(item.get("date") or item.get("postedDate")),
            url=url,
            description=description_clean,
            is_remote=infer_remote(title, location, description_clean),
            salary_is_predicted=False,
            extra={
                "salary_type": str(item.get("salaryType") or ""),
                "job_type": str(item.get("jobType") or ""),
                "expiration_date": parse_date(item.get("expirationDate")) or "",
                "employer_id": str(item.get("employerId") or ""),
                "applications": item.get("applications"),
            },
        )
        return posting


def _bool_param(value: Optional[bool]) -> Optional[str]:
    if value is None:
        return None
    return "true" if value else "false"


def _normalise_contract_type(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in ("permanent", "perm"):
        return "permanent"
    if text in ("contract", "contractor", "freelance"):
        return "contract"
    if text in ("temporary", "temp", "seasonal"):
        return "temporary"
    return "unknown"


def _normalise_contract_time(item: Dict[str, Any]) -> str:
    """Reed expresses full/part time as booleans and/or a ``jobType`` string."""
    job_type = str(item.get("jobType") or "").strip().lower()
    if "part" in job_type:
        return "part_time"
    if "full" in job_type:
        return "full_time"
    if item.get("partTime") is True:
        return "part_time"
    if item.get("fullTime") is True:
        return "full_time"
    return "unknown"
