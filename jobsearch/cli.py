"""Command line interface.

Three verbs: ``search`` (fetch and store new postings), ``detail`` (read one
stored advert), ``shortlist`` (filter and rank what is stored). Every expected
failure exits 1 with a JSON object on stderr::

    {"ok": false, "error": {"code": "missing_credential", "message": "...", "hint": "..."}}

Unknown flags are errors, not ignored: argument parsing runs with
``allow_abbrev=False`` and argparse's own error path is routed through
:class:`~jobsearch.errors.UsageError`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from typing import Any, Dict, List, Optional, Sequence

from . import __version__
from .errors import DetailUnavailableError, JobSearchError, UsageError
from .filters import FilterConfig, evaluate, keywords_from_query, rank
from .guard import scan
from .models import Posting
from .pipeline import SearchRequest, search_all
from .render import render_table
from .sources import SOURCE_NAMES, get_source
from .state import STATUSES, SeenStore
from .textinfer import days_since

PROG = "python3 -m jobsearch"
CLOSED_STATUSES = ("applied", "rejected", "expired")


class StrictParser(argparse.ArgumentParser):
    """ArgumentParser that never abbreviates flags and raises instead of exiting."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> None:  # type: ignore[override]
        raise UsageError(
            message,
            hint="Run '{} --help', or '{} <command> --help'.".format(PROG, PROG),
        )


# --------------------------------------------------------------------------
# Argument types
# --------------------------------------------------------------------------
def _int_at_least(minimum: int):
    def parse(text: str) -> int:
        try:
            value = int(str(text).strip())
        except (TypeError, ValueError):
            raise argparse.ArgumentTypeError(
                "expected a whole number, got {!r}".format(text)
            )
        if value < minimum:
            raise argparse.ArgumentTypeError(
                "must be {} or greater, got {}".format(minimum, value)
            )
        return value

    parse.__name__ = "int>={}".format(minimum)
    return parse


def _text(value: str) -> str:
    cleaned = " ".join(str(value or "").split())
    if not cleaned:
        raise argparse.ArgumentTypeError("must not be empty")
    return cleaned


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = StrictParser(
        prog=PROG,
        description=(
            "UK job search over official job-board APIs: search, store, filter "
            "and rank postings. No scraping, no LLM in the deterministic layer."
        ),
        epilog=(
            "Sources: {}. Keys: see docs/getting-keys.md. "
            "Not affiliated with Adzuna, Reed, Greenhouse, Ashby or Lever.".format(
                ", ".join(SOURCE_NAMES)
            )
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version="jobsearch {}".format(__version__)
    )
    subparsers = parser.add_subparsers(
        dest="command", metavar="{search,detail,shortlist}", title="commands"
    )

    _add_search(subparsers)
    _add_detail(subparsers)
    _add_shortlist(subparsers)
    return parser


def _add_state_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--state",
        metavar="PATH",
        default=None,
        help="seen-postings file (default: ./state/seen.json, or $JOBSEARCH_STATE)",
    )


def _add_search(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[name-defined]
    parser = subparsers.add_parser(
        "search",
        help="fetch postings from one or more sources and store new ones",
        description=(
            "Run every --source, merge and de-duplicate the results, drop "
            "postings already in the state file, and store the rest as 'new'."
        ),
    )
    parser.add_argument(
        "--source",
        dest="sources",
        action="append",
        required=True,
        choices=list(SOURCE_NAMES),
        metavar="SOURCE",
        help="job board to query; repeat for several ({})".format(", ".join(SOURCE_NAMES)),
    )
    parser.add_argument("--query", required=True, type=_text, metavar="TEXT", help="search keywords")
    parser.add_argument(
        "--location",
        default="United Kingdom",
        type=_text,
        metavar="PLACE",
        help="town, city or region (default: United Kingdom)",
    )
    parser.add_argument(
        "--country",
        default=None,
        choices=["gb", "us", "at", "au", "be", "br", "ca", "ch", "de", "es", "fr",
                 "in", "it", "mx", "nl", "nz", "pl", "sg", "za"],
        help="Adzuna country code (default: gb); only valid with --source adzuna",
    )
    parser.add_argument(
        "--limit",
        type=_int_at_least(1),
        default=25,
        metavar="N",
        help="maximum postings per source (default: 25)",
    )
    parser.add_argument(
        "--days",
        type=_int_at_least(0),
        default=14,
        metavar="N",
        help=(
            "provider-side freshness filter where supported, in days "
            "(default: 14; Reed has no such parameter, so use shortlist for "
            "local freshness)"
        ),
    )
    parser.add_argument("--salary-min", type=_int_at_least(0), metavar="GBP", help="provider-side salary floor")
    parser.add_argument("--category", metavar="TAG", help="Adzuna category tag, e.g. it-jobs")
    parser.add_argument(
        "--distance",
        type=_int_at_least(0),
        metavar="MILES",
        help="Reed distance from --location in miles (Reed default: 10)",
    )
    parser.add_argument(
        "--org",
        dest="orgs",
        action="append",
        metavar="SLUG",
        default=None,
        help=(
            "extra ATS employer slug (repeatable); only valid with --source "
            "greenhouse, ashby or lever. Defaults come from data/ats-boards.json"
        ),
    )
    parser.add_argument(
        "--include-seen",
        action="store_true",
        help="also print postings already in the state file",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="do not write to the state file (useful for a first look)",
    )
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    _add_state_option(parser)
    parser.set_defaults(handler=cmd_search)


def _add_detail(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[name-defined]
    parser = subparsers.add_parser(
        "detail",
        help="show one stored posting in full",
        description=(
            "Print the stored advert, its integrity findings and its tracking "
            "fields. Ids may be abbreviated to any unambiguous prefix."
        ),
    )
    parser.add_argument("id", metavar="ID", help="posting id (or unique prefix)")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="re-fetch the full advert (Reed only; Adzuna has no detail endpoint)",
    )
    parser.add_argument(
        "--status",
        choices=list(STATUSES),
        help="set the tracking status after printing",
    )
    parser.add_argument("--note", metavar="TEXT", help="replace the notes field")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    _add_state_option(parser)
    parser.set_defaults(handler=cmd_detail)


def _add_shortlist(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[name-defined]
    parser = subparsers.add_parser(
        "shortlist",
        help="filter stored postings and rank the survivors",
        description=(
            "Apply deterministic filters (freshness, contract/part-time, remote, "
            "salary floor, keywords) to stored postings, then rank survivors by "
            "the documented 100-point score. Applied, rejected and expired "
            "postings are skipped."
        ),
    )
    parser.add_argument(
        "--days",
        type=_int_at_least(0),
        default=30,
        metavar="N",
        help="freshness window in days (default: 30)",
    )
    parser.add_argument("--contract-only", action="store_true", help="keep contract, temporary and part-time roles only")
    parser.add_argument("--part-time-only", action="store_true", help="keep part-time roles only")
    parser.add_argument("--remote-only", action="store_true", help="keep postings with remote wording")
    parser.add_argument("--salary-floor", type=_int_at_least(0), metavar="GBP", help="drop postings whose stated salary maximum is below this figure")
    parser.add_argument(
        "--include-keyword",
        action="append",
        metavar="WORD",
        default=None,
        help="title must contain this word/phrase; repeatable",
    )
    parser.add_argument(
        "--exclude-keyword",
        action="append",
        metavar="WORD",
        default=None,
        help="drop postings whose title contains this word/phrase; repeatable",
    )
    parser.add_argument(
        "--keyword",
        action="append",
        metavar="WORD",
        default=None,
        help="scoring keyword; repeatable (default: words from each posting's search query)",
    )
    parser.add_argument("--min-score", type=_int_at_least(0), default=0, metavar="N", help="drop survivors scoring below N")
    parser.add_argument("--limit", type=_int_at_least(1), default=25, metavar="N", help="maximum rows to print (default: 25)")
    parser.add_argument("--mark", action="store_true", help="mark survivors as shortlisted and store their score")
    parser.add_argument("--explain", action="store_true", help="show score breakdowns and why postings were rejected")
    parser.add_argument("--include-closed", action="store_true", help="include applied/rejected/expired postings")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    _add_state_option(parser)
    parser.set_defaults(handler=cmd_shortlist)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def cmd_search(args: argparse.Namespace) -> int:
    store = SeenStore(args.state)
    known = set(store.load())  # read before the network: a corrupt store fails fast

    # Flags that only one provider understands are an error, not a silent no-op.
    if args.category and "adzuna" not in args.sources:
        raise UsageError(
            "--category is an Adzuna parameter. Add --source adzuna, or drop it."
        )
    if args.distance is not None and "reed" not in args.sources:
        raise UsageError(
            "--distance is a Reed parameter. Add --source reed, or drop it."
        )
    if args.country and "adzuna" not in args.sources:
        raise UsageError(
            "--country is an Adzuna parameter. Add --source adzuna, or drop it."
        )
    if args.orgs and not ({"greenhouse", "ashby", "lever"} & set(args.sources)):
        raise UsageError(
            "--org is an ATS parameter. Add --source greenhouse, ashby or lever, "
            "or drop it."
        )

    clients = []
    for name in args.sources:
        kwargs: Dict[str, Any] = {}
        if name == "adzuna":
            kwargs["country"] = args.country or "gb"
        clients.append(get_source(name, **kwargs))

    request = SearchRequest(
        query=args.query,
        location=args.location,
        limit=args.limit,
        max_days_old=args.days or None,
        salary_min=args.salary_min,
        category=args.category,
        distance=args.distance,
        orgs=args.orgs,
    )
    postings, per_source = search_all(clients, request)

    new_postings = [p for p in postings if p.id not in known]
    if new_postings and not args.no_save:
        store.add(new_postings, query=args.query)

    shown = postings if args.include_seen else new_postings
    already_seen = len(postings) - len(new_postings)

    if args.json:
        print(
            json.dumps(
                {
                    "ok": True,
                    "command": "search",
                    "query": args.query,
                    "location": args.location,
                    "sources": list(args.sources),
                    "per_source": per_source,
                    "fetched": len(postings),
                    "new": len(new_postings),
                    "already_seen": already_seen,
                    "saved": bool(new_postings) and not args.no_save,
                    "state": str(store.path),
                    "results": [posting.to_dict() for posting in shown],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    if shown:
        rows = [
            [
                posting.id,
                posting.source,
                posting.title,
                posting.company or "-",
                posting.location or "-",
                posting.salary_text(),
                posting.posted_date or "-",
            ]
            for posting in shown
        ]
        print(
            render_table(
                ["ID", "SOURCE", "TITLE", "COMPANY", "LOCATION", "SALARY", "POSTED"],
                rows,
                widths=[16, 6, 32, 24, 20, 16, 10],
            )
        )
        print()
    else:
        print("No new postings matched.")

    print(
        "{} fetched, {} new, {} already seen. State: {}".format(
            len(postings), len(new_postings), already_seen, store.path
        )
    )
    if new_postings and args.no_save:
        print("--no-save was set: nothing was written.")
    return 0


def cmd_detail(args: argparse.Namespace) -> int:
    store = SeenStore(args.state)
    entry = store.get(args.id)
    posting = Posting.from_dict(entry)
    refreshed = False

    if args.refresh:
        if posting.source != "reed":
            raise DetailUnavailableError(
                "{} has no single-advert endpoint; --refresh is only supported "
                "for reed.".format(posting.source),
                hint="The stored description is from the search response. Open the advert URL for the full text.",
            )
        if not posting.provider_id:
            raise DetailUnavailableError(
                "This posting has no provider id stored, so it cannot be refreshed.",
                hint="Re-run the search to store ids, or open the advert URL.",
            )
        client = get_source("reed")
        fresh = client.detail(posting.provider_id)
        store.add([fresh], query=str(entry.get("query") or ""))
        posting = Posting.from_dict(store.get(entry["id"]))
        refreshed = True

    if args.status or args.note is not None:
        store.mark(entry["id"], status=args.status, notes=args.note)
        entry = store.get(entry["id"])

    result = scan(posting.description or "")
    age = days_since(posting.posted_date)

    if args.json:
        print(
            json.dumps(
                {
                    "ok": True,
                    "command": "detail",
                    "refreshed": refreshed,
                    "posting": posting.to_dict(),
                    "status": entry.get("status"),
                    "fit": entry.get("fit"),
                    "notes": entry.get("notes") or "",
                    "first_seen": entry.get("first_seen"),
                    "guard": result.as_dict(),
                    "description": result.cleaned_text,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    print("Title:      {}".format(posting.title))
    print("Company:    {}".format(posting.company or "-"))
    print("Location:   {}".format(posting.location or "-"))
    print("Salary:     {}".format(posting.salary_text()))
    print(
        "Posted:     {}{}".format(
            posting.posted_date or "unknown",
            " ({} day{} ago)".format(age, "" if age == 1 else "s")
            if age is not None
            else "",
        )
    )
    contract_line = "Contract:   {} / {}".format(
        posting.contract_type, posting.contract_time
    )
    if posting.source == "reed" and posting.contract_type == "unknown" and not refreshed:
        # Reed's search endpoint omits contract fields; only /jobs/{id} has them.
        contract_line += " (run --refresh for the full advert)"
    print(contract_line)
    print("Remote:     {}".format("yes (inferred)" if posting.is_remote else "no"))
    print(
        "Source:     {} (provider id {})".format(
            posting.source, posting.provider_id or "-"
        )
    )
    print("URL:        {}".format(posting.url or "-"))
    print(
        "Status:     {}{}".format(
            entry.get("status") or "new",
            "   Fit: {}".format(entry["fit"]) if entry.get("fit") is not None else "",
        )
    )
    if entry.get("notes"):
        print("Notes:      {}".format(entry["notes"]))
    print("First seen: {}".format(entry.get("first_seen") or "-"))
    print("Integrity:  {}".format(result.summary()))
    if result.findings:
        for finding in result.findings:
            line = "  - [{}] {}".format(finding.severity, finding.message)
            if finding.evidence:
                line += " | {}".format(finding.evidence)
            print(line)
    print()
    print("--- description ---")
    print(result.cleaned_text or "(no description stored)")
    return 0


def cmd_shortlist(args: argparse.Namespace) -> int:
    store = SeenStore(args.state)
    config = FilterConfig(
        max_age_days=args.days,
        contract_only=args.contract_only,
        part_time_only=args.part_time_only,
        remote_only=args.remote_only,
        salary_floor=args.salary_floor,
        include_keywords=tuple(args.include_keyword or ()),
        exclude_keywords=tuple(args.exclude_keyword or ()),
    )
    keywords_override = list(args.keyword or ())

    entries = [
        entry
        for entry in store.all()
        if args.include_closed or entry.get("status") not in CLOSED_STATUSES
    ]
    by_id = {entry["id"]: entry for entry in entries}
    outcomes = [evaluate(Posting.from_dict(entry), config) for entry in entries]

    def keywords_for(posting: Posting) -> Sequence[str]:
        if keywords_override:
            return keywords_override
        entry = by_id.get(posting.id) or {}
        return keywords_from_query(str(entry.get("query") or ""))

    survivors = rank(
        outcomes,
        keywords_for=keywords_for,
        today=None,
        window_days=max(1, config.max_age_days),
        min_score=args.min_score,
    )
    shown = survivors[: args.limit]
    rejected = [outcome for outcome in outcomes if not outcome.passed]

    if args.mark:
        for outcome, posting_score in shown:
            entry = by_id.get(outcome.posting.id) or {}
            if entry.get("status") == "new":
                store.mark(
                    outcome.posting.id,
                    status="shortlisted",
                    fit=posting_score.total,
                )

    if args.json:
        payload: Dict[str, Any] = {
            "ok": True,
            "command": "shortlist",
            "state": str(store.path),
            "filters": {
                "days": config.max_age_days,
                "contract_only": config.contract_only,
                "part_time_only": config.part_time_only,
                "remote_only": config.remote_only,
                "salary_floor": config.salary_floor,
                "include_keywords": list(config.include_keywords),
                "exclude_keywords": list(config.exclude_keywords),
                "min_score": args.min_score,
            },
            "considered": len(entries),
            "survivors": len(survivors),
            "marked": bool(args.mark),
            "results": [
                dict(
                    outcome.posting.to_dict(),
                    score=posting_score.as_dict(),
                    warnings=outcome.warnings,
                )
                for outcome, posting_score in shown
            ],
        }
        if args.explain:
            payload["rejected"] = [
                {
                    "id": outcome.posting.id,
                    "title": outcome.posting.title,
                    "failures": outcome.failures,
                }
                for outcome in rejected
            ]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if not shown:
        print(
            "No postings passed the filters ({} considered, {} rejected).".format(
                len(entries), len(rejected)
            )
        )
        if args.explain:
            _print_rejections(rejected)
        elif rejected:
            print("Re-run with --explain to see why each was rejected.")
        return 0

    rows = []
    for outcome, posting_score in shown:
        posting = outcome.posting
        flags = []
        if posting.contract_type != "unknown":
            flags.append(posting.contract_type)
        if posting.contract_time == "part_time":
            flags.append("part-time")
        if posting.is_remote:
            flags.append("remote")
        rows.append(
            [
                str(posting_score.total),
                posting.id,
                posting.title,
                posting.company or "-",
                posting.salary_text(),
                posting.posted_date or "-",
                ",".join(flags) or "-",
            ]
        )
    print(
        render_table(
            ["SCORE", "ID", "TITLE", "COMPANY", "SALARY", "POSTED", "FLAGS"],
            rows,
            widths=[5, 16, 36, 20, 16, 10, 18],
        )
    )
    print()
    print(
        "{} considered, {} passed filters, {} shown. Marked: {}".format(
            len(entries), len(survivors), len(shown), "yes" if args.mark else "no"
        )
    )

    if args.explain:
        print("\nScore breakdown")
        for outcome, posting_score in shown:
            print(
                "\n{}  {}  [{}]".format(
                    outcome.posting.id, outcome.posting.title, posting_score.total
                )
            )
            for reason in posting_score.reasons:
                print("  - {}".format(reason))
            for warning in outcome.warnings:
                print("  ! {}".format(warning))
        if rejected:
            print("\nRejected")
            _print_rejections(rejected)
    return 0


def _print_rejections(rejected: List[Any]) -> None:
    if not rejected:
        return
    print("\nRejected")
    for outcome in rejected:
        print("  {}  {}".format(outcome.posting.id, outcome.posting.title))
        for failure in outcome.failures:
            print("    - {}".format(failure))


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args_list = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parser.parse_args(args_list)
        if not getattr(args, "command", None):
            raise UsageError(
                "a command is required",
                hint="Use one of: search, detail, shortlist.",
            )
        handler = getattr(args, "handler", None)
        if handler is None:  # pragma: no cover - argparse guarantees a handler
            raise UsageError("Unknown command {!r}".format(args.command))
        return int(handler(args))
    except JobSearchError as exc:
        _print_error(exc.as_dict())
        return exc.exit_code
    except KeyboardInterrupt:
        _print_error({"code": "interrupted", "message": "Interrupted by user"})
        return 130
    except Exception as exc:  # noqa: BLE001 - last-resort guard for the CLI
        if os.environ.get("JOBSEARCH_DEBUG"):
            traceback.print_exc()
        _print_error(
            {
                "code": "internal_error",
                "message": "{}: {}".format(type(exc).__name__, exc),
                "hint": "This is a bug. Re-run with JOBSEARCH_DEBUG=1 for a traceback.",
            }
        )
        return 1


def _print_error(payload: Dict[str, Any]) -> None:
    print(
        json.dumps({"ok": False, "error": payload}, ensure_ascii=False),
        file=sys.stderr,
    )
