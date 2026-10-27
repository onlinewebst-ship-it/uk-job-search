"""Posting-integrity gate.

Job adverts are untrusted input. They are written by whoever posts them, they
reach you through a third-party API, and anything downstream (a scoring rubric, a
shortlisting LLM, a human skim) will read them. This module looks for the tricks
that make an advert say one thing to a human and another to a machine:

* content hidden with CSS or the ``hidden`` attribute
* zero-width and bidirectional control characters
* Unicode "tag" characters (U+E0000-U+E007F), which are invisible but can encode
  a whole ASCII sentence
* lines shaped like instructions to an AI ("ignore previous instructions",
  "you are an AI", "system prompt")
* words mixing Latin and Cyrillic letters, the classic look-alike spoof
  (``pаypal`` with a Cyrillic ``а``)

It **advises, never blocks**: :func:`scan` returns a verdict plus a cleaned
text, and the caller decides what to do. The cleaned text removes what a human
would not see (hidden nodes, invisible characters) but deliberately keeps
visible prose that looks instruction-shaped, so the evidence stays in front of
the reviewer instead of being silently deleted.

Adapted from the concept in ``ai-job-search-dev/ai-job-search`` (MIT); the
implementation here is independent. See ATTRIBUTION.md.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# Character classes
# --------------------------------------------------------------------------
ZERO_WIDTH = {
    0x00AD: "SOFT HYPHEN",
    0x180E: "MONGOLIAN VOWEL SEPARATOR",
    0x200B: "ZERO WIDTH SPACE",
    0x200C: "ZERO WIDTH NON-JOINER",
    0x200D: "ZERO WIDTH JOINER",
    0x2060: "WORD JOINER",
    0xFEFF: "ZERO WIDTH NO-BREAK SPACE (BOM)",
}
BIDI_CONTROLS = {
    0x061C: "ARABIC LETTER MARK",
    0x200E: "LEFT-TO-RIGHT MARK",
    0x200F: "RIGHT-TO-LEFT MARK",
    0x202A: "LEFT-TO-RIGHT EMBEDDING",
    0x202B: "RIGHT-TO-LEFT EMBEDDING",
    0x202C: "POP DIRECTIONAL FORMATTING",
    0x202D: "LEFT-TO-RIGHT OVERRIDE",
    0x202E: "RIGHT-TO-LEFT OVERRIDE",
    0x2066: "LEFT-TO-RIGHT ISOLATE",
    0x2067: "RIGHT-TO-LEFT ISOLATE",
    0x2068: "FIRST STRONG ISOLATE",
    0x2069: "POP DIRECTIONAL ISOLATE",
}
TAG_CHAR_FIRST = 0xE0000
TAG_CHAR_LAST = 0xE007F

# Hidden-content CSS. Each entry is (human label, compiled pattern).
_HIDDEN_STYLE_RULES: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("display:none", re.compile(r"display\s*:\s*none", re.I)),
    ("visibility:hidden", re.compile(r"visibility\s*:\s*hidden", re.I)),
    ("opacity:0", re.compile(r"opacity\s*:\s*0(?:\.0+)?\s*(?:;|!|$)", re.I)),
    ("font-size:0", re.compile(r"font-size\s*:\s*0(?:px|pt|em|rem|%)?\s*(?:;|!|$)", re.I)),
    (
        "zero-size clip",
        re.compile(
            r"clip\s*:\s*rect\s*\(\s*0(?:px)?[\s,]+0(?:px)?[\s,]+0(?:px)?[\s,]+0(?:px)?\s*\)",
            re.I,
        ),
    ),
    ("clip-path off-screen", re.compile(r"clip-path\s*:\s*inset\s*\(\s*(?:50|100)%", re.I)),
    ("transform scaled to zero", re.compile(r"transform\s*:\s*scale\(\s*0\s*\)", re.I)),
    ("hidden by offset", re.compile(r"(?:left|text-indent)\s*:\s*-\d{3,}px", re.I)),
    (
        "zero height with overflow hidden",
        re.compile(r"height\s*:\s*0(?:px)?\s*;[^;]*overflow\s*:\s*hidden", re.I),
    ),
)

_SKIP_TAGS = frozenset({"script", "style", "template", "noscript", "svg", "head", "iframe"})
_BLOCK_TAGS = frozenset(
    {
        "p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article",
        "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote",
        "pre", "hr", "dd", "dt", "figure", "figcaption", "main", "aside", "nav",
        "form", "fieldset", "address", "details", "summary",
    }
)

# Instruction-shaped lines. (pattern, label, severity)
_INSTRUCTION_RULES: Tuple[Tuple["re.Pattern[str]", str, str], ...] = (
    (
        re.compile(
            r"\b(?:ignore|disregard|forget)\s+(?:all\s+)?(?:of\s+)?(?:the\s+)?"
            r"(?:previous|prior|above|earlier|preceding)\s+"
            r"(?:instructions?|prompts?|rules?|directions?)\b",
            re.I,
        ),
        "ignore-previous-instructions",
        "high",
    ),
    (
        re.compile(
            r"\byou\s+are\s+(?:now\s+)?(?:an?\s+)?"
            r"(?:ai|a\.i\.|artificial intelligence|assistant|language model|llm|"
            r"chatbot|ai assistant|ai agent)\b",
            re.I,
        ),
        "you-are-an-ai",
        "high",
    ),
    (
        re.compile(r"\bsystem\s+prompt\b|\bprompt\s+injection\b", re.I),
        "system-prompt",
        "medium",
    ),
    (
        re.compile(r"\bnew\s+instructions?\s*:", re.I),
        "new-instructions",
        "high",
    ),
    (
        re.compile(r"<\|\s*(?:im_start|im_end|system|endoftext|assistant)\s*\|>", re.I),
        "chat-template-token",
        "high",
    ),
    (
        re.compile(
            r"\b(?:send|email|forward|share|reveal)\s+(?:me\s+)?(?:your|the)\s+"
            r"(?:api[\s-]?key|secret|password|credentials?|access\s+token)\b",
            re.I,
        ),
        "credential-request",
        "high",
    ),
    (
        re.compile(
            r"\bdo\s+not\s+(?:tell|inform|mention\s+(?:this\s+)?to)\s+"
            r"(?:the\s+)?(?:user|candidate|human|recruiter)\b",
            re.I,
        ),
        "conceal-from-human",
        "medium",
    ),
)

SCRIPT_PREFIXES = {"LATIN", "CYRILLIC", "GREEK"}
_WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
_TAG_LIKE_RE = re.compile(r"<\s*[a-zA-Z][a-zA-Z0-9]*(?:\s[^<>]*)?>")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


# --------------------------------------------------------------------------
# Result objects
# --------------------------------------------------------------------------
@dataclass
class Finding:
    """One integrity problem, with evidence a human can check."""

    code: str
    severity: str  # "low" | "medium" | "high"
    message: str
    evidence: str = ""
    count: int = 1
    location: str = ""

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "count": self.count,
        }
        if self.evidence:
            payload["evidence"] = self.evidence
        if self.location:
            payload["location"] = self.location
        return payload


@dataclass
class GuardResult:
    """Verdict for one posting."""

    verdict: str  # "clean" | "suspicious" | "high_risk"
    findings: List[Finding] = field(default_factory=list)
    cleaned_text: str = ""
    stats: Dict[str, Any] = field(default_factory=dict)

    @property
    def codes(self) -> List[str]:
        return sorted({finding.code for finding in self.findings})

    @property
    def is_clean(self) -> bool:
        return not self.findings

    def of_severity(self, severity: str) -> List[Finding]:
        return [f for f in self.findings if f.severity == severity]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "findings": [finding.as_dict() for finding in self.findings],
            "stats": dict(self.stats),
        }

    def summary(self) -> str:
        if not self.findings:
            return "clean"
        return "{} ({})".format(
            self.verdict.replace("_", " "), ", ".join(self.codes)
        )


# --------------------------------------------------------------------------
# HTML to visible text
# --------------------------------------------------------------------------
class _VisibleTextExtractor(HTMLParser):
    """Collect visible text, remembering text that was hidden and why."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.visible: List[str] = []
        self.hidden: List[Tuple[str, str, str]] = []  # (reason, tag, text)
        self._stack: List[Tuple[str, Optional[str], int]] = []
        self._buffers: List[Dict[str, Any]] = []

    # -- parser callbacks ---------------------------------------------------
    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        attributes = {name.lower(): (value or "") for name, value in attrs}
        if tag in _BLOCK_TAGS:
            self._newline()
        if tag == "li":
            self._append("- ")
        reason = self._hidden_reason(tag, attributes)
        depth = len(self._stack)
        self._stack.append((tag, reason, depth))
        if reason:
            self._buffers.append(
                {"reason": reason, "tag": tag, "depth": depth, "parts": []}
            )

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        # <img/>, <br/> etc: no text content, but a hidden self-closing element
        # is not interesting on its own.
        if tag in _BLOCK_TAGS:
            self._newline()

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self._newline()
        while self._stack:
            frame_tag, reason, depth = self._stack.pop()
            if reason:
                self._close_buffer(depth)
            if frame_tag == tag:
                break

    def handle_data(self, data: str) -> None:
        if not data:
            return
        self._append(data)

    def close(self) -> None:  # type: ignore[override]
        super().close()
        while self._stack:
            _, reason, depth = self._stack.pop()
            if reason:
                self._close_buffer(depth)

    # -- helpers ------------------------------------------------------------
    def _hidden_reason(self, tag: str, attributes: Dict[str, str]) -> Optional[str]:
        if tag in _SKIP_TAGS:
            return "{} element (never rendered)".format(tag)
        if "hidden" in attributes:
            return "hidden attribute"
        if attributes.get("aria-hidden", "").lower() == "true":
            return "aria-hidden=true"
        style = attributes.get("style", "")
        if style:
            for label, pattern in _HIDDEN_STYLE_RULES:
                if pattern.search(style):
                    return label
        return None

    def _append(self, text: str) -> None:
        if self._buffers:
            self._buffers[-1]["parts"].append(text)
        else:
            self.visible.append(text)

    def _newline(self) -> None:
        self._append("\n")

    def _close_buffer(self, depth: int) -> None:
        for index in range(len(self._buffers) - 1, -1, -1):
            buffer = self._buffers[index]
            if buffer["depth"] != depth:
                continue
            del self._buffers[index]
            text = " ".join("".join(buffer["parts"]).split())
            if not text:
                return
            if self._buffers:
                # Nested inside another hidden element: keep the text with the
                # outer finding rather than reporting it twice.
                self._buffers[-1]["parts"].append(" " + text)
            else:
                self.hidden.append((buffer["reason"], buffer["tag"], text))
            return


def html_to_text(html: str) -> str:
    """Return the visible, whitespace-normalised text of an HTML fragment.

    Hidden elements are dropped, because the point is to read the advert the
    way a person would see it.
    """
    visible, _ = _extract_visible(html)
    return visible


def _extract_visible(markup: str) -> Tuple[str, List[Finding]]:
    parser = _VisibleTextExtractor()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # pragma: no cover - HTMLParser is very forgiving
        return _fallback_strip(markup), []

    raw = "".join(parser.visible)
    findings = [
        Finding(
            code="hidden_content",
            severity="medium",
            message="text hidden from readers via {}".format(reason),
            evidence=_clip(text),
            location="<{}>".format(tag),
        )
        for reason, tag, text in parser.hidden
    ]
    return _tidy(raw), findings


def _fallback_strip(markup: str) -> str:
    text = re.sub(r"<[^>]+>", " ", markup)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return _tidy(text)


def _tidy(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.replace("\r", "\n").split("\n")]
    lines = [line for line in lines if line]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------
def scan(text: str, html: Optional[bool] = None) -> GuardResult:
    """Inspect a posting body and return findings plus a cleaned text.

    ``html`` defaults to auto-detection (any tag-like construct means HTML).
    Pass ``html=False`` for plain text so angle brackets in prose are not
    mistaken for markup.
    """
    raw = str(text or "")
    if html is None:
        html = bool(_TAG_LIKE_RE.search(raw))

    findings: List[Finding] = []
    if html:
        visible, hidden_findings = _extract_visible(raw)
        findings.extend(hidden_findings)
    else:
        visible = _tidy(raw)

    visible = _CONTROL_RE.sub("", visible)

    # 1. Unicode tag characters: decode the payload, then strip.
    tag_hits = _find_tag_chars(visible)
    if tag_hits:
        positions, decoded = tag_hits
        findings.append(
            Finding(
                code="unicode_tags",
                severity="high",
                message=(
                    "{} invisible Unicode tag character(s); these can carry "
                    "hidden text past a human reader".format(len(positions))
                ),
                evidence=_clip(decoded) if decoded else _clip(visible, 80),
                count=len(positions),
            )
        )
        visible = _strip_codepoints(visible, range(TAG_CHAR_FIRST, TAG_CHAR_LAST + 1))

    # 2. Zero-width characters.
    zero_hits = _find_codepoints(visible, ZERO_WIDTH)
    if zero_hits:
        findings.append(
            Finding(
                code="zero_width",
                severity="medium",
                message="{} zero-width character(s) embedded in the text".format(
                    len(zero_hits)
                ),
                evidence=_marked_evidence(visible, zero_hits),
                count=len(zero_hits),
            )
        )
        visible = _strip_codepoints(visible, ZERO_WIDTH)

    # 3. Bidi controls.
    bidi_hits = _find_codepoints(visible, BIDI_CONTROLS)
    if bidi_hits:
        findings.append(
            Finding(
                code="bidi_control",
                severity="medium",
                message="{} bidirectional control character(s); these can reverse "
                "how text displays".format(len(bidi_hits)),
                evidence=_marked_evidence(visible, bidi_hits),
                count=len(bidi_hits),
            )
        )
        visible = _strip_codepoints(visible, BIDI_CONTROLS)

    visible = _tidy(visible)

    # 4. Instruction-shaped lines (reported, never removed).
    for finding in _scan_instruction_lines(visible):
        findings.append(finding)

    # 5. Latin/Cyrillic look-alike words.
    for finding in _scan_mixed_script(visible):
        findings.append(finding)

    stats = {
        "characters": len(visible),
        "lines": len(visible.splitlines()),
        "hidden_blocks": sum(1 for f in findings if f.code == "hidden_content"),
        "removed_characters": max(0, len(raw) - len(visible)),
    }

    if any(f.severity == "high" for f in findings):
        verdict = "high_risk"
    elif findings:
        verdict = "suspicious"
    else:
        verdict = "clean"

    return GuardResult(
        verdict=verdict, findings=findings, cleaned_text=visible, stats=stats
    )


def _find_codepoints(text: str, mapping: Dict[int, str]) -> List[int]:
    return [index for index, char in enumerate(text) if ord(char) in mapping]


def _find_tag_chars(text: str) -> Optional[Tuple[List[int], str]]:
    positions: List[int] = []
    decoded: List[str] = []
    for index, char in enumerate(text):
        code = ord(char)
        if TAG_CHAR_FIRST <= code <= TAG_CHAR_LAST:
            positions.append(index)
            # U+E0020..U+E007E mirror printable ASCII; U+E0001..U+E001F are
            # language/format tags with no readable equivalent.
            if 0xE0020 <= code <= 0xE007E:
                decoded.append(chr(code - TAG_CHAR_FIRST))
            elif code == 0xE0001:
                decoded.append("[LANGUAGE TAG]")
    if not positions:
        return None
    return positions, "".join(decoded).strip()


def _strip_codepoints(text: str, codepoints) -> str:
    doomed = set(codepoints)
    return "".join(char for char in text if ord(char) not in doomed)


def _marked_evidence(text: str, positions: List[int], width: int = 40) -> str:
    """Show the text around the first hit with every invisible char named."""
    if not positions:
        return ""
    first = positions[0]
    start = max(0, first - width)
    end = min(len(text), first + width)
    chunk = text[start:end]
    out: List[str] = []
    for char in chunk:
        code = ord(char)
        if code in ZERO_WIDTH or code in BIDI_CONTROLS:
            out.append("<U+{:04X}>".format(code))
        else:
            out.append(char)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return _clip(prefix + "".join(out) + suffix)


def _scan_instruction_lines(text: str) -> List[Finding]:
    findings: List[Finding] = []
    seen: set = set()
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        for pattern, label, severity in _INSTRUCTION_RULES:
            if not pattern.search(stripped):
                continue
            key = (label, stripped[:120])
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                Finding(
                    code="instruction_like",
                    severity=severity,
                    message="line reads as an instruction to an AI ({}); review "
                    "the advert before letting a model act on it".format(label),
                    evidence=_clip(stripped),
                    location="line {}".format(number),
                )
            )
            break
    return findings


def _scan_mixed_script(text: str) -> List[Finding]:
    findings: List[Finding] = []
    reported = set()
    for match in _WORD_RE.finditer(text):
        word = match.group(0)
        scripts = set()
        for char in word:
            name = unicodedata.name(char, "")
            for prefix in SCRIPT_PREFIXES:
                if name.startswith(prefix):
                    scripts.add(prefix)
                    break
        if "LATIN" in scripts and "CYRILLIC" in scripts:
            key = word.lower()
            if key in reported:
                continue
            reported.add(key)
            marked = "".join(
                (
                    "<CYR:{}>".format(char)
                    if unicodedata.name(char, "").startswith("CYRILLIC")
                    else char
                )
                for char in word
            )
            findings.append(
                Finding(
                    code="mixed_script",
                    severity="high",
                    message=(
                        "word {!r} mixes Latin and Cyrillic letters; this is a "
                        "look-alike spoofing pattern".format(word)
                    ),
                    evidence=_clip(marked),
                )
            )
    return findings


def _clip(value: str, limit: int = 200) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "\u2026"
