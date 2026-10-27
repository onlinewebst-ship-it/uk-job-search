# Attribution

## Upstream project

This repository is a from-scratch UK reimplementation inspired by:

**[ai-job-search-dev/ai-job-search](https://github.com/ai-job-search-dev/ai-job-search)**
Licence: MIT.

That project targets the Danish job market and is built around Claude Code as
the interface. This one targets the UK, has no runtime dependencies, and keeps
the deterministic layer (search, filter, score) separate from any model use.

**No source code from the upstream project is included here.** The ideas below
were adapted; everything in `jobsearch/` was written for this repository.

## Which ideas were adapted

| Area | What was adapted | How this implementation differs |
| --- | --- | --- |
| **Evaluation rubric structure** (`prompts/01-evaluation.md`) | Hard gates before scoring, then weighted dimensions, a numeric threshold table, and a fixed verdict template | Rebuilt for the UK: right-to-work and language gates, UK salary bands and sources, IR35 status (inside/outside) and day-rate arithmetic |
| **Posting-integrity gate concept** (`jobsearch/guard.py`) | The idea that advert text is untrusted and should be checked for hidden content, invisible characters and instruction-shaped text aimed at an agent | Independent implementation on `html.parser`: CSS and attribute-based hiding, zero-width and bidi controls, decoded Unicode tag characters, instruction-line patterns, Latin/Cyrillic look-alike detection, plus a cleaned-text output and severity model |
| **Seen-postings state and de-duplication pattern** (`jobsearch/state.py`, `jobsearch/pipeline.py`) | Keeping a local record of what has already been seen, keyed by a stable per-posting id, so re-runs are digests rather than repeats | JSON store with atomic writes and backups, idempotent enrichment, per-posting tracking fields (status, fit, notes), and cross-board de-duplication on (title, company, location) |
| **Prompt pack layout** (`prompts/`) | Separating evaluation, writing style and interview preparation into three focused documents | Rewritten for UK conventions: no em-dashes, banned cliches, employer-claim verification, the interview backtrack test, UK CV and cover-letter norms |

## What is original to this repository

- The Adzuna and Reed clients, written against each provider's published API
  documentation, including Reed's HTTP Basic auth with a blank password and
  Adzuna's paging behaviour.
- The deterministic scoring arithmetic (keyword 45, freshness 20, salary
  presence 20, remote 15) and the filter semantics, including the documented
  treatment of unknown dates and unknown salaries.
- The CLI contract: strict flag validation, JSON errors on stderr, exit code 1,
  id-prefix resolution, and `--no-save` / `--include-seen` behaviour.
- The stdlib-only HTTP client with credential redaction in error messages.
- The UK market and ethics documentation.

## Third-party data and trademarks

- Adzuna data and API: Adzuna Ltd, <https://www.adzuna.com>. Not affiliated.
- Reed data and API: Reed Online Ltd, <https://www.reed.co.uk>. Not affiliated.

Adzuna and Reed are trademarks of their respective owners. This project claims
no rights over their data. Users obtain their own API keys and are bound by the
providers' terms; see `docs/ethics-and-terms.md`.

## Licence

This repository is MIT licensed (see `LICENSE`). The upstream project is also
MIT licensed; its licence and copyright notice apply to its own code, none of
which is redistributed here.
