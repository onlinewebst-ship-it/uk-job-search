"""Small text/date helpers shared by sources, filters and the guard.

Nothing in here talks to the network and nothing is clever: these are the
deterministic transformations the rest of the framework relies on, kept apart
so they can be tested on their own.
"""

from __future__ import annotations

import datetime as _dt
import html as _html
import re
from typing import Iterable, List, Optional

# Words that carry no signal when scoring keyword overlap (UK job-ad English).
STOPWORDS = frozenset(
    """
    a an and any are as at be been being both but by can could do does for from
    has have how i if in into is it its job jobs just more most my no not of on
    or our out over per role roles so some such than that the their them then
    there these they this to up us was we were what when where which who will
    with within work working you your
    """.split()
)

_REMOTE_RE = re.compile(
    r"\b(fully[\s-]+remote|remote[\s-]+first|remote|work[\s-]+from[\s-]+home|"
    r"wfh|home[\s-]+based|telecommute|teleworking|anywhere[\s-]+in[\s-]+the[\s-]+uk)\b",
    re.IGNORECASE,
)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\u00a0]+")

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d/%m/%y",
    "%d-%m-%Y",
    "%d %B %Y",
    "%d %b %Y",
    "%Y/%m/%d",
)


def plain_text(value: str) -> str:
    """Cheap HTML-to-text for scoring and previews.

    Strips tags and entities but keeps the words in hidden elements: use
    :func:`jobsearch.guard.scan` when hidden content must be excluded.
    """
    if not value:
        return ""
    text = _TAG_RE.sub(" ", str(value))
    text = _html.unescape(text)
    return " ".join(text.split())


def normalise_space(value: str) -> str:
    return " ".join(str(value or "").split())


def parse_date(value) -> Optional[str]:
    """Normalise a provider date to ``YYYY-MM-DD``; ``None`` if unparseable.

    Handles ISO 8601 timestamps with ``Z`` or offsets (Adzuna), UK
    ``dd/mm/yyyy`` strings (Reed) and plain ``yyyy-mm-dd`` dates.
    """
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value.date().isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None

    iso_candidate = text.replace("Z", "+00:00")
    try:
        parsed = _dt.datetime.fromisoformat(iso_candidate)
        return parsed.date().isoformat()
    except ValueError:
        pass
    try:
        parsed_date = _dt.date.fromisoformat(text[:10])
        return parsed_date.isoformat()
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return _dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def days_since(posted_date, today=None) -> Optional[int]:
    """Whole days between ``posted_date`` and ``today`` (default: today).

    Returns ``None`` when the date is missing or unparseable. Future dates
    clamp to 0 so a board's timezone slip cannot produce a negative age.
    """
    iso = parse_date(posted_date)
    if not iso:
        return None
    reference = today or _dt.date.today()
    if isinstance(reference, _dt.datetime):
        reference = reference.date()
    delta = (reference - _dt.date.fromisoformat(iso)).days
    return max(0, delta)


def infer_remote(*parts: str) -> bool:
    """Infer a remote flag from advert text.

    Neither Adzuna nor Reed exposes a dependable remote field on the endpoints
    used here, so the flag is inferred from the title, location and the first
    part of the description. This deliberately does not match "hybrid": a
    hybrid role still requires commuting, and treating it as remote would make
    the ``--remote-only`` filter lie.
    """
    haystack = " ".join(p for p in parts if p)
    if not haystack:
        return False
    return bool(_REMOTE_RE.search(haystack))


WHOLE_UK_NAMES = frozenset(
    {
        "united kingdom",
        "united kingdom of great britain and northern ireland",
        "uk",
        "u.k.",
        "gb",
        "great britain",
        "britain",
        "nationwide",
        "anywhere in the uk",
        "anywhere in uk",
    }
)


def is_whole_uk(location: str) -> bool:
    """True when a location means "the whole UK" rather than a place.

    Adzuna and Reed are UK-only in this framework (the ATS sources are not), and
    sending ``where``/
    ``locationName`` a country name makes their geocoders guess. Omitting the
    parameter is the reliable way to search the entire country.
    """
    text = " ".join(str(location or "").lower().split()).strip(" .,")
    return text in WHOLE_UK_NAMES


def keyword_tokens(text: str) -> List[str]:
    """Lowercase content words, used to turn a search query into keywords."""
    words = re.findall(r"[a-z0-9][a-z0-9+.#-]*", str(text or "").lower())
    tokens: List[str] = []
    for word in words:
        cleaned = word.strip("-.#")
        if len(cleaned) < 2 or cleaned in STOPWORDS:
            continue
        if cleaned not in tokens:
            tokens.append(cleaned)
    return tokens


def contains_phrase(haystack: str, phrase: str) -> bool:
    """Case-insensitive whole-word/phrase match.

    Uses word boundaries around alphanumeric phrases so ``"ai"`` does not match
    ``"said"``, while phrases such as ``"c++"`` fall back to a plain substring
    test because ``\\b`` is meaningless next to a symbol.
    """
    haystack_l = " ".join(str(haystack or "").lower().split())
    phrase_l = " ".join(str(phrase or "").lower().split())
    if not phrase_l:
        return False
    if re.fullmatch(r"[a-z0-9][a-z0-9 .&/+-]*", phrase_l):
        pattern = r"(?<![a-z0-9])" + re.escape(phrase_l) + r"(?![a-z0-9])"
        return re.search(pattern, haystack_l) is not None
    return phrase_l in haystack_l


def dedupe_preserving_order(items: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
