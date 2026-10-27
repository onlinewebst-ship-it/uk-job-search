"""Deterministic filtering and scoring.

Nothing here calls a model, the network, or the clock (unless you let it).
Every decision is a pure function of the posting, the configuration and the
``today`` date you pass in, which is what makes the shortlist reproducible and
the unit tests stable.

Filtering
---------
:func:`evaluate` returns a pass/fail outcome *plus* every failure and warning,
so the CLI can explain a rejection instead of silently dropping a job:

==========================  =============================================
``max_age_days``            advert must be at most N days old; an unknown
                            date fails because recency cannot be confirmed
``contract_only``           keep contract, temporary and part-time roles
``part_time_only``          keep part-time roles only
``remote_only``             keep postings whose text implies remote work
``salary_floor``            known maximum must reach the floor; an unknown
                            salary passes with a warning (it cannot be proved
                            to fail)
``include_keywords``        title must contain at least one
``exclude_keywords``        title must contain none
==========================  =============================================

Scoring
-------
100 points, four transparent components (see :class:`Weights`):

* keyword overlap 45 - share of the supplied keywords present in title+body
* freshness 20 - linear decay to zero across the freshness window
* salary presence 20 - stated range beats a single figure beats nothing;
  board-estimated salaries score half
* remote 15 - the inferred remote flag

The score ranks survivors. It is not a fit judgement: the rubric in
``prompts/01-evaluation.md`` is where judgement happens, and a human (or an
LLM, invoked deliberately) makes the call.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .errors import UsageError
from .models import Posting
from .textinfer import contains_phrase, days_since, dedupe_preserving_order, keyword_tokens

# Structured contract values that count as "contract/part-time only".
CONTRACT_LIKE_TYPES = frozenset({"contract", "temporary"})
# Title hints for UK contract roles. Boards frequently leave contract_type
# unset, but a contract advert almost always says one of these in the title.
CONTRACT_TITLE_HINTS = (
    "contract",
    "contractor",
    "day rate",
    "interim",
    "freelance",
    "inside ir35",
    "outside ir35",
)


@dataclass(frozen=True)
class FilterConfig:
    """All deterministic filters in one immutable object."""

    max_age_days: int = 30
    contract_only: bool = False
    part_time_only: bool = False
    remote_only: bool = False
    salary_floor: Optional[int] = None
    include_keywords: Tuple[str, ...] = ()
    exclude_keywords: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.max_age_days < 0:
            raise UsageError("--days must be zero or greater")
        if self.salary_floor is not None and self.salary_floor < 0:
            raise UsageError("--salary-floor must be zero or greater")
        object.__setattr__(
            self,
            "include_keywords",
            tuple(
                dedupe_preserving_order(
                    [k.strip() for k in self.include_keywords if k and k.strip()]
                )
            ),
        )
        object.__setattr__(
            self,
            "exclude_keywords",
            tuple(
                dedupe_preserving_order(
                    [k.strip() for k in self.exclude_keywords if k and k.strip()]
                )
            ),
        )


@dataclass
class Outcome:
    """The result of evaluating one posting against a :class:`FilterConfig`."""

    posting: Posting
    passed: bool
    failures: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def as_dict(self, score: Optional["Score"] = None) -> Dict[str, object]:
        payload: Dict[str, object] = {
            "id": self.posting.id,
            "passed": self.passed,
            "failures": list(self.failures),
            "warnings": list(self.warnings),
        }
        if score is not None:
            payload["score"] = score.as_dict()
        return payload


@dataclass(frozen=True)
class Weights:
    """Points available per scoring component. They sum to 100."""

    keyword: int = 45
    freshness: int = 20
    salary: int = 20
    remote: int = 15

    def total(self) -> int:
        return self.keyword + self.freshness + self.salary + self.remote


@dataclass
class Score:
    """A posting's score with the arithmetic kept for inspection."""

    total: int
    components: Dict[str, Dict[str, object]] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
        return {
            "total": self.total,
            "components": self.components,
            "reasons": list(self.reasons),
        }


def evaluate(
    posting: Posting, config: FilterConfig, today=None
) -> Outcome:
    """Apply every hard filter; collect all failures rather than short-circuiting."""
    failures: List[str] = []
    warnings: List[str] = []
    title = posting.title or ""

    # 1. Include keywords (title only: a mention in the body is not the role).
    if config.include_keywords:
        matched = [
            keyword
            for keyword in config.include_keywords
            if contains_phrase(title, keyword)
        ]
        if not matched:
            failures.append(
                "title lacks any include keyword ({})".format(
                    ", ".join(config.include_keywords)
                )
            )

    # 2. Exclude keywords (title only).
    excluded = [
        keyword
        for keyword in config.exclude_keywords
        if contains_phrase(title, keyword)
    ]
    if excluded:
        failures.append(
            "title contains excluded keyword(s): {}".format(", ".join(excluded))
        )

    # 3. Freshness.
    age = days_since(posting.posted_date, today)
    if age is None:
        failures.append("posted date unknown; cannot confirm the freshness window")
    elif age > config.max_age_days:
        failures.append(
            "too old: posted {} days ago (window {} days)".format(
                age, config.max_age_days
            )
        )

    # 4. Contract / part-time.
    title_says_contract = any(
        contains_phrase(title, hint) for hint in CONTRACT_TITLE_HINTS
    )
    is_part_time = posting.contract_time == "part_time" or contains_phrase(
        title, "part time"
    ) or contains_phrase(title, "part-time")
    is_contract_like = posting.contract_type in CONTRACT_LIKE_TYPES or title_says_contract

    if config.part_time_only and not is_part_time:
        failures.append(
            "not part-time (contract_time={}, title={!r})".format(
                posting.contract_time, title
            )
        )
    elif config.contract_only and not (is_contract_like or is_part_time):
        failures.append(
            "not contract/part-time (contract_type={}, contract_time={})".format(
                posting.contract_type, posting.contract_time
            )
        )

    # 5. Remote.
    if config.remote_only and not posting.is_remote:
        failures.append("not remote (no remote wording in title, location or body)")

    # 6. Salary floor.
    if config.salary_floor is not None:
        top = (
            posting.salary_max
            if posting.salary_max is not None
            else posting.salary_min
        )
        if top is None:
            warnings.append(
                "no salary stated; cannot confirm the {} floor".format(
                    config.salary_floor
                )
            )
        elif top < config.salary_floor:
            failures.append(
                "salary tops out at {:,} which is below the {:,} floor".format(
                    top, config.salary_floor
                )
            )
        if posting.salary_is_predicted:
            warnings.append("salary is a board estimate, not stated by the employer")

    return Outcome(
        posting=posting, passed=not failures, failures=failures, warnings=warnings
    )


def _round(value: float) -> int:
    """Half-up rounding.

    Python's built-in ``round`` is banker's rounding (``round(22.5) == 22``),
    which would make a 1-of-2 keyword match score lower than the arithmetic
    suggests. Scores are shown to users, so they round the way people expect.
    """
    return int(math.floor(value + 0.5))


def keyword_match(keywords: Sequence[str], text: str) -> Tuple[List[str], List[str]]:
    """Return (matched, missing) keywords for ``text``."""
    matched: List[str] = []
    missing: List[str] = []
    for keyword in keywords:
        if contains_phrase(text, keyword):
            matched.append(keyword)
        else:
            missing.append(keyword)
    return matched, missing


def score(
    posting: Posting,
    keywords: Sequence[str] = (),
    weights: Weights = Weights(),
    today=None,
    window_days: int = 30,
) -> Score:
    """Score one posting out of ``weights.total()`` (100 by default)."""
    components: Dict[str, Dict[str, object]] = {}
    reasons: List[str] = []
    total = 0.0

    # --- keyword overlap ---------------------------------------------------
    clean_keywords = [k for k in dedupe_preserving_order(keywords) if k.strip()]
    haystack = "{} {}".format(posting.title or "", posting.description or "")
    if clean_keywords:
        matched, missing = keyword_match(clean_keywords, haystack)
        ratio = len(matched) / float(len(clean_keywords))
        awarded = weights.keyword * ratio
        components["keyword"] = {
            "awarded": _round(awarded),
            "possible": weights.keyword,
            "matched": matched,
            "missing": missing,
        }
        reasons.append(
            "keywords {}/{} matched ({}): {:.0f}/{}".format(
                len(matched),
                len(clean_keywords),
                ", ".join(matched) if matched else "none",
                awarded,
                weights.keyword,
            )
        )
    else:
        awarded = 0.0
        components["keyword"] = {
            "awarded": 0,
            "possible": weights.keyword,
            "matched": [],
            "missing": [],
        }
        reasons.append("keywords: none supplied, 0/{}".format(weights.keyword))
    total += awarded

    # --- freshness ---------------------------------------------------------
    age = days_since(posting.posted_date, today)
    if age is None:
        fresh_award = 0.0
        reasons.append("freshness: posted date unknown, 0/{}".format(weights.freshness))
    elif window_days <= 0:
        fresh_award = float(weights.freshness) if age <= 0 else 0.0
        reasons.append(
            "freshness: {} day(s) old, {:.0f}/{}".format(
                age, fresh_award, weights.freshness
            )
        )
    else:
        decay = max(0.0, 1.0 - (age / float(window_days)))
        fresh_award = weights.freshness * decay
        reasons.append(
            "freshness: {} day(s) old in a {} day window, {:.1f}/{}".format(
                age, window_days, fresh_award, weights.freshness
            )
        )
    components["freshness"] = {
        "awarded": _round(fresh_award),
        "possible": weights.freshness,
        "age_days": age,
        "window_days": window_days,
    }
    total += fresh_award

    # --- salary presence ---------------------------------------------------
    if posting.salary_min is not None and posting.salary_max is not None:
        if posting.salary_min != posting.salary_max:
            base = float(weights.salary)
            detail = "stated range"
        else:
            base = weights.salary * 0.75
            detail = "single stated figure"
    elif posting.salary_min is not None or posting.salary_max is not None:
        base = weights.salary * 0.6
        detail = "one bound stated"
    else:
        base = 0.0
        detail = "no salary stated"
    if posting.salary_is_predicted and base:
        base *= 0.5
        detail += " (board estimate, half credit)"
    components["salary"] = {
        "awarded": _round(base),
        "possible": weights.salary,
        "detail": detail,
        "salary_text": posting.salary_text(),
    }
    reasons.append(
        "salary: {}, {:.1f}/{}".format(detail, base, weights.salary)
    )
    total += base

    # --- remote ------------------------------------------------------------
    remote_award = float(weights.remote) if posting.is_remote else 0.0
    components["remote"] = {
        "awarded": _round(remote_award),
        "possible": weights.remote,
        "is_remote": bool(posting.is_remote),
    }
    reasons.append(
        "remote: {} {:.0f}/{}".format(
            "yes" if posting.is_remote else "no", remote_award, weights.remote
        )
    )
    total += remote_award

    return Score(total=_round(total), components=components, reasons=reasons)


def rank(
    outcomes: Iterable[Outcome],
    keywords_for: Callable[[Posting], Sequence[str]] = lambda p: (),
    weights: Weights = Weights(),
    today=None,
    window_days: int = 30,
    min_score: int = 0,
) -> List[Tuple[Outcome, Score]]:
    """Score, filter by ``min_score`` and sort survivors.

    Order: score descending, then newest first, then title A-Z. The title
    tie-break exists so two identical runs always print in the same order.
    """
    ranked: List[Tuple[Outcome, Score]] = []
    for outcome in outcomes:
        if not outcome.passed:
            continue
        posting_score = score(
            outcome.posting,
            keywords_for(outcome.posting),
            weights=weights,
            today=today,
            window_days=window_days,
        )
        if posting_score.total < min_score:
            continue
        ranked.append((outcome, posting_score))
    # Stable three-pass sort: title A-Z, then newest first, then score high-low.
    ranked.sort(key=lambda pair: pair[0].posting.title.lower())
    ranked.sort(key=lambda pair: pair[0].posting.posted_date or "", reverse=True)
    ranked.sort(key=lambda pair: pair[1].total, reverse=True)
    return ranked


def keywords_from_query(query: str) -> List[str]:
    """Turn a search phrase into scoring keywords."""
    return keyword_tokens(query)
