"""Normalised job posting model shared by every source client.

Each source client maps its provider payload into a single :class:`Posting`, so
that filtering, scoring, de-duplication and storage never need to know which
job board a posting came from.

Identity
--------
``Posting.id`` is a stable hash of ``(source, provider id)``. Provider ids are
only unique within one board, so the hash includes the source name. The same
job advertised on two boards therefore has two ids; the CLI removes those
near-duplicates at merge time (see ``jobsearch.pipeline.dedupe``).
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional

SOURCE_NAMES = ("adzuna", "reed")
CONTRACT_TYPES = ("permanent", "contract", "temporary", "unknown")
CONTRACT_TIMES = ("full_time", "part_time", "unknown")

_FIELDS = (
    "id",
    "source",
    "provider_id",
    "title",
    "company",
    "location",
    "salary_min",
    "salary_max",
    "currency",
    "contract_type",
    "contract_time",
    "posted_date",
    "url",
    "description",
    "is_remote",
    "salary_is_predicted",
)


def stable_id(source: str, provider_id: Any) -> str:
    """Return a short, stable id for a posting from ``source``.

    ``sha256`` of ``"<source>:<provider id>"``, truncated to 16 hex characters
    (64 bits). Deterministic across runs and machines, so the seen-store stays
    valid when the repository is copied to another computer.
    """
    key = "{}:{}".format(str(source).strip().lower(), str(provider_id).strip())
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _as_int(value: Any) -> Optional[int]:
    """Coerce a provider salary field to a whole number, or ``None``.

    Adzuna returns floats, Reed returns ints or nulls, and both occasionally
    return strings. Anything unparseable becomes ``None`` rather than raising:
    a missing salary must never break a search.
    """
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return int(round(number))


@dataclass
class Posting:
    """One job advert in normalised form.

    Attributes
    ----------
    id:
        Stable hash of ``(source, provider_id)`` - the key used by the store.
    provider_id:
        The board's own id (Adzuna ad id, Reed job id).
    contract_type:
        One of ``permanent``, ``contract``, ``temporary``, ``unknown``.
    contract_time:
        One of ``full_time``, ``part_time``, ``unknown``. Kept separate from
        ``contract_type`` because they are independent axes: a role can be a
        part-time permanent position.
    salary_is_predicted:
        True when the board estimated the salary instead of reading it from the
        advert (Adzuna sets ``salary_is_predicted``). Scoring treats predicted
        salaries as weaker evidence than stated ones.
    is_remote:
        Inferred from the advert text by :func:`jobsearch.textinfer.
        infer_remote`, because neither Adzuna nor Reed exposes a reliable
        remote flag on these endpoints.
    """

    id: str
    source: str
    title: str
    company: str = ""
    location: str = ""
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    currency: str = "GBP"
    contract_type: str = "unknown"
    contract_time: str = "unknown"
    posted_date: Optional[str] = None
    url: str = ""
    description: str = ""
    is_remote: bool = False
    provider_id: str = ""
    salary_is_predicted: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.salary_min = _as_int(self.salary_min)
        self.salary_max = _as_int(self.salary_max)
        # A one-sided salary ("from GBP 40,000") stays one-sided: collapsing it
        # to a range would overstate what the advert actually promises, and the
        # scorer gives less credit for one bound than for two.
        if self.salary_min is not None and self.salary_max is not None:
            if self.salary_min > self.salary_max:
                self.salary_min, self.salary_max = self.salary_max, self.salary_min
        if self.contract_type not in CONTRACT_TYPES:
            self.contract_type = "unknown"
        if self.contract_time not in CONTRACT_TIMES:
            self.contract_time = "unknown"
        self.title = " ".join(str(self.title or "").split())
        self.company = " ".join(str(self.company or "").split())
        self.location = " ".join(str(self.location or "").split())
        if not self.id:
            self.id = stable_id(self.source, self.provider_id or self.url)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["extra"] = dict(self.extra or {})
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Posting":
        """Rebuild a posting from stored JSON, ignoring unknown keys."""
        kwargs = {key: data[key] for key in _FIELDS if key in data}
        kwargs["extra"] = dict(data.get("extra") or {})
        return cls(**kwargs)

    def salary_text(self) -> str:
        """Human-readable salary, e.g. ``£45,000-£55,000`` or ``Not stated``."""
        symbol = {"GBP": "\u00a3", "EUR": "\u20ac", "USD": "$"}.get(
            (self.currency or "GBP").upper(), ""
        )
        prefix = symbol or ((self.currency or "") + " ")
        if self.salary_min is None and self.salary_max is None:
            return "Not stated"
        if self.salary_min is not None and self.salary_max is not None:
            if self.salary_min == self.salary_max:
                value = "{}{:,}".format(prefix, self.salary_min)
            else:
                value = "{}{:,}-{}{:,}".format(
                    prefix, self.salary_min, prefix, self.salary_max
                )
        else:
            only = self.salary_min if self.salary_min is not None else self.salary_max
            value = "{}{:,}".format(prefix, only)
        if self.salary_is_predicted:
            value += " (est.)"
        return value

    def age_days(self, today) -> Optional[int]:
        """Age in whole days from ``posted_date``, or ``None`` if unknown."""
        from .textinfer import days_since

        return days_since(self.posted_date, today)
