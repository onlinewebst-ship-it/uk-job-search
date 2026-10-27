"""The seen-postings store.

A single JSON file (default ``./state/seen.json``, override with ``--state`` or
``JOBSEARCH_STATE``) holding one record per posting, keyed by
``Posting.id``. JSON rather than JSONL because every operation rewrites the
whole set anyway (an atomic replace), and a single object is easier to inspect,
back up and hand-edit.

Guarantees
----------
* **Idempotent.** Running the same search twice never creates duplicates and
  never resets a status set by the user.
* **Additive.** ``add`` only creates records or enriches them: an existing
  ``first_seen``, ``status``, ``fit`` or ``notes`` is never overwritten.
* **Atomic.** Writes go to a temporary file in the same directory and are
  moved into place with ``os.replace``, so a crash mid-write cannot truncate
  the store. The previous file is kept as ``<name>.bak``.
* **Loud about corruption.** A store that is not valid JSON raises
  :class:`~jobsearch.errors.StateError` instead of being silently replaced.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .errors import AmbiguousIdError, NotFoundError, StateError, UsageError
from .models import Posting

DEFAULT_STATE_PATH = "state/seen.json"
STATUSES = ("new", "shortlisted", "applied", "rejected", "expired")
FORMAT_VERSION = 1


def _utc_now() -> str:
    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


class SeenStore:
    """Read/write access to the seen-postings file."""

    def __init__(self, path: Optional[str] = None) -> None:
        raw = path or os.environ.get("JOBSEARCH_STATE") or DEFAULT_STATE_PATH
        self.path = Path(raw).expanduser()
        self._entries: Optional[Dict[str, Dict[str, Any]]] = None
        self._extra: Dict[str, Any] = {}

    # ------------------------------------------------------------------ load
    def load(self, refresh: bool = False) -> Dict[str, Dict[str, Any]]:
        """Return the entry mapping, reading the file on first use."""
        if self._entries is not None and not refresh:
            return self._entries
        if not self.path.exists():
            self._entries = {}
            self._extra = {}
            return self._entries
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StateError(
                "Could not read the state file {}: {}".format(self.path, exc),
                hint="Check file permissions, or point --state at a writable path.",
            )
        if not text.strip():
            self._entries = {}
            self._extra = {}
            return self._entries
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise StateError(
                "State file {} is not valid JSON ({}).".format(self.path, exc),
                hint=(
                    "Refusing to overwrite it. Fix or move the file, or restore "
                    "{} if it exists.".format(str(self.path) + ".bak")
                ),
                context={"path": str(self.path)},
            )

        if isinstance(payload, dict) and isinstance(payload.get("postings"), dict):
            entries = payload["postings"]
            self._extra = {
                k: v for k, v in payload.items() if k not in ("postings",)
            }
        elif isinstance(payload, dict):
            # Tolerate a bare {id: entry} mapping produced by hand or by an
            # earlier version of this tool.
            entries = payload
            self._extra = {}
        else:
            raise StateError(
                "State file {} has an unexpected top-level {}.".format(
                    self.path, type(payload).__name__
                ),
                hint="Expected a JSON object with a 'postings' mapping.",
            )

        for key, entry in entries.items():
            if not isinstance(entry, dict):
                raise StateError(
                    "State entry {!r} in {} is not an object.".format(
                        str(key)[:40], self.path
                    ),
                    hint="Fix the record by hand; nothing has been overwritten.",
                )
            entry.setdefault("id", key)
        self._entries = entries
        return self._entries

    # ------------------------------------------------------------------ read
    def entries(self) -> Dict[str, Dict[str, Any]]:
        return self.load()

    def all(self) -> List[Dict[str, Any]]:
        """Entries sorted oldest-first (stable, so output is reproducible)."""
        entries = list(self.load().values())
        entries.sort(key=lambda e: (str(e.get("first_seen") or ""), e.get("id") or ""))
        return entries

    def get(self, reference: str) -> Dict[str, Any]:
        return self.load()[self.resolve(reference)]

    def resolve(self, reference: str) -> str:
        """Resolve a full id or an unambiguous id prefix."""
        ref = str(reference or "").strip()
        if not ref:
            raise UsageError("A posting id is required")
        entries = self.load()
        if ref in entries:
            return ref
        matches = [key for key in entries if key.startswith(ref)]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise NotFoundError(
                "No stored posting matches {!r}".format(ref),
                hint=(
                    "Run 'python3 -m jobsearch search' first, or list ids with "
                    "'python3 -m jobsearch shortlist --json'."
                ),
                context={"state": str(self.path)},
            )
        raise AmbiguousIdError(
            "Id prefix {!r} matches {} postings".format(ref, len(matches)),
            hint="Use more characters of the id.",
            context={"matches": sorted(matches)[:10]},
        )

    def counts(self) -> Dict[str, int]:
        counts = {status: 0 for status in STATUSES}
        counts["total"] = 0
        for entry in self.load().values():
            status = str(entry.get("status") or "new")
            counts[status] = counts.get(status, 0) + 1
            counts["total"] += 1
        return counts

    # ----------------------------------------------------------------- write
    def add(
        self,
        postings: Iterable[Posting],
        query: Optional[str] = None,
        now: Optional[str] = None,
    ) -> List[str]:
        """Store new postings; enrich existing ones. Returns the new ids.

        Existing records keep their ``first_seen``, ``status``, ``fit`` and
        ``notes``. A stored description is replaced only when the incoming one
        is longer (search snippets are shorter than detail responses), so a
        refresh can never degrade what is already stored.
        """
        entries = self.load()
        stamp = now or _utc_now()
        added: List[str] = []
        for posting in postings:
            if not isinstance(posting, Posting):
                raise StateError("add() expects Posting objects")
            record = entries.get(posting.id)
            if record is None:
                entries[posting.id] = self._new_entry(posting, query, stamp)
                added.append(posting.id)
                continue
            record["last_seen"] = stamp
            self._enrich(record, posting, query)
        if added or postings:
            self.save()
        return added

    def mark(
        self,
        reference: str,
        status: Optional[str] = None,
        notes: Optional[str] = None,
        fit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Update ``status``, ``notes`` and/or ``fit`` for one stored posting."""
        key = self.resolve(reference)
        if status is None and notes is None and fit is None:
            raise UsageError("mark() needs at least one of status, notes or fit")
        if status is not None and status not in STATUSES:
            raise UsageError(
                "Unknown status {!r}".format(status),
                hint="Valid statuses: {}".format(", ".join(STATUSES)),
            )
        entry = self.load()[key]
        if status is not None:
            entry["status"] = status
        if notes is not None:
            entry["notes"] = str(notes)
        if fit is not None:
            entry["fit"] = _clamp_fit(fit)
        entry["updated_at"] = _utc_now()
        self.save()
        return entry

    def save(self) -> None:
        """Write the store atomically."""
        entries = self.load()
        payload = dict(self._extra)
        payload.update(
            {
                "version": FORMAT_VERSION,
                "updated_at": _utc_now(),
                "postings": entries,
            }
        )
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing = None
        if self.path.exists():
            try:
                existing = self.path.read_text(encoding="utf-8")
            except OSError:
                existing = None
        handle, tmp_name = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as tmp_file:
                tmp_file.write(text + "\n")
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            if existing:
                backup = self.path.with_suffix(self.path.suffix + ".bak")
                backup.write_text(existing, encoding="utf-8")
            os.replace(tmp_name, self.path)
        except OSError as exc:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise StateError(
                "Could not write the state file {}: {}".format(self.path, exc),
                hint="Check that the directory exists and is writable.",
            )

    # --------------------------------------------------------------- helpers
    def _new_entry(
        self, posting: Posting, query: Optional[str], stamp: str
    ) -> Dict[str, Any]:
        entry = posting.to_dict()
        entry.update(
            {
                "first_seen": stamp,
                "last_seen": stamp,
                "query": query or "",
                "fit": None,
                "status": "new",
                "notes": "",
            }
        )
        return entry

    def _enrich(
        self, record: Dict[str, Any], posting: Posting, query: Optional[str]
    ) -> None:
        incoming = posting.to_dict()
        for key, value in incoming.items():
            if key in ("first_seen", "last_seen", "status", "notes", "fit", "query"):
                continue
            if key == "description":
                if len(str(value or "")) > len(str(record.get(key) or "")):
                    record[key] = value
                continue
            if value in (None, "", [], {}) and record.get(key) not in (None, "", [], {}):
                continue
            record[key] = value
        if query and not record.get("query"):
            record["query"] = query


def _clamp_fit(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise UsageError("--fit must be an integer 0-100")
    if number < 0 or number > 100:
        raise UsageError("--fit must be between 0 and 100")
    return number
