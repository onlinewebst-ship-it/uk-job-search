# The UK job-board API landscape

This framework supports only boards that publish a **free, official, documented
API** that an individual can register for without a partner agreement. That
constraint is deliberate: scraping is brittle, frequently breaches terms, and
breaks the moment a site changes its markup.

## Supported

| Board | API status | Free? | What you get | Notes |
| --- | --- | --- | --- | --- |
| **Adzuna** | Official v1 REST API, documented at developer.adzuna.com | Yes, with a daily call allowance | Aggregated adverts from many UK boards; title, company, location, salary (sometimes estimated), truncated description, redirect URL | UK coverage is broad but it is an aggregator: direct-employer adverts only appear if the employer posts through a partner board. No single-advert endpoint, so descriptions are snippets. |
| **Reed** | Official Jobseeker API, documented at reed.co.uk/developers | Yes | Direct employer and agency adverts; search plus a full single-advert endpoint (`/api/1.0/jobs/{jobId}`) with complete description, contract type and salary type | Strong for direct employers and public sector. Search results omit contract type; the detail call fills it in. |

Both are queried over HTTPS with an API key, both return JSON, and both are
subject to their own terms (see `ethics-and-terms.md`).

## Deliberately not supported

| Board | Why not |
| --- | --- |
| **LinkedIn** | There is no public jobs API. LinkedIn's User Agreement and its Prohibition of Scraping and Automation terms forbid automated reading of job listings. Automating a logged-in session also risks the account. It is excluded on purpose, not for lack of effort. Use LinkedIn by hand, or through its own job alerts. |
| **Indeed** | Indeed retired its public Publisher API. There is no free, documented route for an individual developer. Any "Indeed scraper" is working against the terms. |
| **Totaljobs / CWJobs / Jobsite** | StepStone-owned. API access is partner-only, under commercial agreement. |
| **CV-Library** | Partner/integration-only API; no public self-service access. |
| **Glassdoor** | Partner API, not open to individuals; jobs data is licensed. |
| **Monster** | Partner API; no public self-service access for the UK market. |
| **Otta / Welcome to the Jungle** | No public API. |
| **Guardian Jobs, CharityJob, Civil Service Jobs, NHS Jobs** | No public API for job search. NHS Jobs and Civil Service Jobs have their own search sites and alerts; use them directly. |
| **DWP "Find a job"** | No public API. |
| **Recruiter/ATS sites (Workable, Greenhouse, Lever, Ashby)** | Each has a public **board** endpoint per company, which is legitimate to read, but there are thousands of employers and no single index. Out of scope here, and a reasonable next extension. |

## Why the distinction matters

- **Official API** means the provider expects programmatic access, documents the
  parameters, and can revoke a key rather than block an IP. It is stable enough
  to build on.
- **Partner-only** means the data is licensed. Using it without an agreement is a
  contract problem, not a technical one.
- **Scraping** means parsing HTML that nobody promised to keep stable, usually
  against the site's terms, and often behind bot detection. It is also the
  fastest way to get an account banned.

If a board is not in the supported list, the honest answer is: check whether it
publishes API documentation. Look for a `/developers`, `/api` or `/partners`
page, read the terms, and only then decide.

## Coverage caveats worth knowing

- **Aggregators duplicate.** The same advert can appear on Adzuna and Reed, from
  the agency and from the employer, with different wording. `jobsearch search`
  de-duplicates on (title, company, location) across boards and on the provider
  id within a board, but it cannot catch every reworded duplicate.
- **Recency differs.** Adzuna supports a `max_days_old` filter; Reed's search
  endpoint does not have a posted-since parameter. That is why `search --days`
  applies provider-side where supported, and `shortlist --days` applies the
  freshness window locally to everything stored.
- **Salaries are inconsistent.** Adzuna returns annualised figures and flags
  estimates; Reed returns the advert's own figure plus a salary type (per annum,
  per day, per hour). Hourly and daily figures are not annual salaries, and the
  framework does not convert them. Check the units before comparing.
- **Remote is inferred.** Neither API exposes a reliable remote flag on these
  endpoints, so `is_remote` is inferred from wording in the title, location and
  description. Hybrid roles are deliberately not counted as remote.
- **Descriptions are truncated on search.** Reed's detail endpoint returns the
  full text; Adzuna does not have one. Use `--refresh` for Reed postings, and
  the advert URL for Adzuna.
- **Agencies hide the employer.** "Company not disclosed" adverts cannot be
  de-duplicated across boards, and often repeat the same role under several
  recruiters.

## Adding a board

1. Confirm the board publishes a free, documented API that you can register for.
2. Read its terms; check whether commercial use or redistribution is restricted.
3. Add a client in `jobsearch/sources/` with `search()` and `detail()` returning
   `Posting` objects, following the shape of `reed.py`.
4. Register it in `jobsearch/sources/__init__.py` and add a `search_source`
   mapping in `jobsearch/pipeline.py`.
5. Add fixtures and tests that run offline, plus a note in this file.
