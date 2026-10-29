# uk-job-search

A small, dependency-free framework for running a UK job search from the
terminal. It queries job boards through their **official, free APIs**, stores
what it finds, filters and ranks the results with transparent rules, and hands
you a clean record of each advert to take into the judgement stage.

No scraping. No browser automation. No language model in the deterministic
layer. Two API keys, one JSON state file, and plain Python.

```
$ python3 -m jobsearch search --source adzuna --source reed --query "AI trainer"
ID                SOURCE  TITLE                             COMPANY                   LOCATION              SALARY            POSTED
----------------  ------  --------------------------------  ------------------------  --------------------  ----------------  ----------
4ba2dfb45d6a5565  reed    AI Trainer                        Halcyon Learning          Leeds, West Yorkshi…  £32,000-£38,000   2026-10-24
f35f0a593ee23a77  reed    Data Quality Analyst              Orbit Systems             Remote                Not stated        2026-10-12
fb2d143583eac8b4  reed    Administrator                     Tidewater Council         Bristol               £24,000           2026-09-01
57cd27b5e8a5e70c  adzuna  AI Trainer (Remote)               Northwind Analytics Ltd   London, Central Lon…  £42,000-£48,000   2026-10-24
541ced6e1c786ada  adzuna  Contract Data Annotator           Bright Meridian Consult…  Manchester, Greater…  £250-£300 (est.)  2026-10-20
aeef94dbf3651d02  adzuna  Senior Machine Learning Engineer  -                         Edinburgh, Scotland   Not stated        2026-09-01

6 fetched, 6 new, 0 already seen. State: state/seen.json
```

(Example output, generated from the JSON fixtures in `tests/fixtures` so it can be
reproduced without an API key.)

## Who it is for

- People running a **UK** job search who want their own pipeline rather than a
  board's algorithm deciding what they see.
- Developers who want a small, readable base to extend with another board, a
  scoring tweak, or an application tracker.
- Anyone who wants the LLM part of job hunting kept **optional and explicit**,
  rather than baked into the tool that decides what you look at.

## Requirements

- Python **3.9 or newer**. Nothing else. The runtime and the tests use only the
  standard library (`urllib`, `json`, `argparse`, `unittest`, `dataclasses`).
- An API key from [Adzuna](https://developer.adzuna.com/signup) and/or
  [Reed](https://www.reed.co.uk/developers/jobseeker). Reed's is free; Adzuna's
  registers free but is evaluation-only (25 calls) until you subscribe. Step by
  step: [`docs/getting-keys.md`](docs/getting-keys.md).

No `pip install`, no virtualenv required:

```sh
git clone <your-fork-url> uk-job-search
cd uk-job-search
export ADZUNA_APP_ID='...'
export ADZUNA_APP_KEY='...'
export REED_API_KEY='...'
python3 -m jobsearch --help
```

## Quick start

```sh
# Search one board, whole UK
python3 -m jobsearch search --source reed --query "AI trainer"

# Search both boards, merge and de-duplicate, store what is new
python3 -m jobsearch search --source adzuna --source reed --query "AI trainer" --limit 50

# Company careers pages: no key, no signup. Uses data/ats-boards.json
python3 -m jobsearch search --source greenhouse --source ashby --query "engineer"

# Add one employer for this run only (the slug in its careers-page URL)
python3 -m jobsearch search --source ashby --query "evaluation" --org mistral

# Look at the new postings without writing anything
python3 -m jobsearch search --source adzuna --query "evaluation" --no-save

# Read one advert in full (ids accept a unique prefix)
python3 -m jobsearch detail 6f2c1d

# Rank what you have stored
python3 -m jobsearch shortlist --days 21 --remote-only --salary-floor 40000

# Contract and part-time roles only, with the reasoning shown
python3 -m jobsearch shortlist --contract-only --explain --mark

# Machine-readable output for anything
python3 -m jobsearch shortlist --json
```

`search` writes new postings to `state/seen.json` and prints only the ones it had
not seen before, so a daily run is a digest rather than the same list again. The
state file is plain JSON, keyed by a stable hash of `(source, provider id)`.

## Sources

There are two kinds of source, and they behave differently.

| Source | Key | What it is | Notes |
| --- | --- | --- | --- |
| `reed` | free API key | Job board | UK only. The search endpoint returns no contract fields, so part-time/contract filtering has little to work with. |
| `adzuna` | free to register, **paid for volume** (25 test calls, then a subscription) | Aggregator | 20 countries. Carries contract type and part-time, and estimated salaries. |
| `greenhouse`, `ashby`, `lever` | **none** | Employer ATS | A company's own careers page. Fetched whole, then filtered locally. |

**Why the ATS sources matter.** Adzuna and Reed search an index; Greenhouse,
Ashby and Lever *are* the employer's vacancy list. There is no query parameter
and no key, so the tool fetches each configured board and filters for you:
every keyword word must appear in title or description, and the location filter
keeps UK, UK-city, EMEA and worldwide-remote postings while dropping roles tied
to another country.

The employer list is `data/ats-boards.json`, mapping each provider's slug to a
display name. Edit it freely, point `UKJOBSEARCH_ATS_BOARDS` at your own file, or
add one employer for a single run with `--org <slug>`. One request is made per
board per search, and a board that fails is named in the error rather than
silently skipped.

Caveat: Greenhouse's board endpoint exposes `updated_at` (last touched), not the
original posting date. Ashby and Lever give true publication dates. The stored
`extra.date_source` says which was used, so treat Greenhouse freshness as weak
evidence.

## Commands

| Command | What it does |
| --- | --- |
| `search` | Runs one or more sources, merges and de-duplicates, drops postings already in the state file, stores the rest as `new`. |
| `detail <id>` | Prints one stored advert: fields, tracking status, integrity findings, cleaned description. `--refresh` re-fetches Reed postings in full; `--status` and `--note` update tracking. |
| `shortlist` | Applies deterministic filters to stored postings and ranks the survivors by the score below. `--mark` persists `shortlisted` plus the score. |

Global behaviour: unknown flags are errors (exit 1, JSON on stderr), not
silently ignored, and source-specific flags (`--country`, `--category` for
Adzuna; `--distance` for Reed) are rejected when the matching `--source` is
missing rather than quietly doing nothing. Every expected failure is JSON:

```json
{"ok": false, "error": {"code": "missing_credential", "message": "ADZUNA_APP_ID is not set; the Adzuna source cannot run without it.", "hint": "Get a free key at https://developer.adzuna.com/signup ..."}}
```

Use `--json` on any command for machine-readable output, and `--state PATH` (or
`JOBSEARCH_STATE`) to keep more than one search campaign apart.

## What the score means

100 points, four components, all deterministic and printed on request
(`shortlist --explain`):

| Component | Points | How it is awarded |
| --- | --- | --- |
| **Keyword overlap** | 45 | Share of the supplied keywords (or the words from the search query that found the posting) present in title plus description. Whole-word, case-insensitive. |
| **Freshness** | 20 | Linear decay across the freshness window: full marks on the day it was posted, zero at the window edge, zero if the date is unknown. |
| **Salary presence** | 20 | 20 for a stated range, 15 for a single stated figure, 12 for one bound, 0 for nothing. Adzuna's *estimated* salaries score half. |
| **Remote** | 15 | 15 if the advert's text implies remote work, 0 otherwise. Hybrid does not count. |

The score is a **ranking device, not a fit judgement**. It tells you which of
the postings you have already decided are plausible deserve your attention
first. Fit is a human decision, and `prompts/01-evaluation.md` is the rubric for
it.

Filters are separate from scoring and are hard pass/fail:

| Flag | Rule |
| --- | --- |
| `--days N` | Advert must be at most N days old. An unknown date fails. |
| `--contract-only` | Contract, temporary or part-time roles only. Title hints ("contract", "day rate", "inside IR35", ...) count when the board leaves the field unset. |
| `--part-time-only` | Part-time only, from `contract_time` or the title. |
| `--remote-only` | Advert text must imply remote work. |
| `--salary-floor N` | Known maximum must reach N. A missing salary passes **with a warning**, because it cannot be proved to fail. |
| `--include-keyword` / `--exclude-keyword` | Title must contain at least one / none. Repeatable. |

## The integrity gate

Job adverts are untrusted input, and an LLM reading one is a prompt-injection
target. `detail` runs a gate over each advert that reports:

- text hidden with CSS (`display:none`, `visibility:hidden`, `opacity:0`,
  `font-size:0`, zero-size clips, off-screen positioning) or the `hidden` and
  `aria-hidden` attributes;
- zero-width and bidirectional control characters;
- Unicode tag characters (`U+E0000` to `U+E007F`), which are invisible and can
  encode an ASCII sentence (the report decodes them);
- lines shaped like instructions to an AI ("ignore previous instructions",
  "you are an AI", "system prompt");
- words mixing Latin and Cyrillic letters, the look-alike spoofing pattern.

It **advises, never blocks**. The cleaned text strips what a human would not see
but keeps visible prose that looks instruction-shaped, so the evidence stays in
front of you. Findings are reported, not acted on.

## The prompt pack

`prompts/` contains three UK-adapted prompts, written to be used deliberately,
not automatically:

| File | Use |
| --- | --- |
| `01-evaluation.md` | Fit rubric: hard gates (right to work, language) first, then five weighted dimensions, with IR35 and UK salary reality-checking. |
| `02-writing-style.md` | Application writing rules: no em-dashes, banned cliches, no unsupported claims, verify employer claims, the "interview backtrack test". |
| `03-interview-prep.md` | STAR story bank, stage-specific preparation, honest handling of gaps. |

They are inputs to a conversation you choose to have. The CLI never calls a
model.

## What this deliberately does not do

- **No LinkedIn.** No public jobs API, and its terms forbid automation. See
  [`docs/uk-market.md`](docs/uk-market.md).
- **No Indeed.** Its public Publisher API was retired; there is no legitimate
  free route.
- **No scraping, no browser automation, no captcha or proxy evasion.** Official
  APIs only, and nothing that circumvents a block.
- **No LLM in the deterministic layer.** Filtering and scoring are pure Python
  with unit tests. The prompts are separate and optional.
- **No automated applications.** It prepares the ground; you write and send.
- **No candidate data.** The repository contains no CVs, names, emails or
  phone numbers, and none are required to run it.

## Privacy

`state/seen.json` holds advert text and your notes, and is excluded from git by
default. It stays on your machine. Do not commit it, and delete it when a search
campaign ends. Keys are read from environment variables and never written to
disk by this tool.

## Tests

Fully offline, no keys, no network:

```sh
python3 -m unittest discover -s tests -v
```

214 tests cover response parsing for all five sources, query construction and
authentication, missing-key errors, state idempotency and durability, every
filter, the scoring arithmetic, de-duplication, the integrity gate, the ATS
board and location filtering, and the CLI end to end (including the JSON error
contract).

## Layout

```
jobsearch/
  cli.py            argument parsing, three commands, JSON error contract
  models.py         Posting dataclass, stable ids
  sources/          adzuna.py, reed.py, base.py
  pipeline.py       run sources, merge, de-duplicate
  filters.py        deterministic filters and the 100-point score
  guard.py          posting-integrity gate
  state.py          JSON seen-postings store (atomic writes)
  http.py           urllib JSON client with actionable errors
  render.py         plain-text tables
  textinfer.py      dates, remote inference, keyword tokens
prompts/            evaluation, writing style, interview prep
docs/               getting keys, UK market, ethics and terms
tests/              unittest suites and JSON fixtures
```

## Upstream credit

Inspired by [`ai-job-search-dev/ai-job-search`](https://github.com/ai-job-search-dev/ai-job-search),
an MIT-licensed Danish-market project built around Claude Code. This is an
independent UK reimplementation using only the standard library. See
[`ATTRIBUTION.md`](ATTRIBUTION.md) for exactly which ideas were adapted.

## Not affiliated

This project is **not affiliated with, endorsed by, or sponsored by Adzuna or
Reed**. Adzuna and Reed are trademarks of their respective owners. You obtain
your own API keys and accept their terms when you do. See
[`docs/ethics-and-terms.md`](docs/ethics-and-terms.md).

## Licence

MIT. Copyright (c) 2026 Online Web Studio Ltd. See [`LICENSE`](LICENSE).
