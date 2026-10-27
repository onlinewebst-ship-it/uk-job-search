# Ethics, terms and data protection

This tool talks to two commercial APIs on your behalf. Using it well means
knowing what you agreed to when you registered for a key, and behaving like a
guest on someone else's infrastructure.

Nothing here is legal advice. Read the provider's own terms, which are the
authoritative version, and are the ones you accepted when you signed up.

## 1. Respect rate limits

- Both providers cap requests. Adzuna's account starts on a 25-call evaluation
  plan and moves to a paid allowance when you subscribe — the figures are on
  your dashboard. Reed applies its own limits and returns HTTP 429 when you
  exceed them.
- The tool makes one request per page of results. `--limit 25` is one call; a
  large `--limit` with Adzuna pages through results in blocks of 50.
- Do not loop the CLI in a tight polling cycle. A job search once or twice a day
  is plenty: adverts do not appear by the second, and repeated identical queries
  burn the allowance you need for the queries that matter.
- On HTTP 429, wait. The error message tells you what happened. Retrying
  immediately, rotating keys, or switching IPs to get around a limit is abuse,
  and it is how individual access gets withdrawn.
- Cache what you have already seen. That is what the state file is for: a re-run
  that finds nothing new still costs API calls, so run it on a sensible cadence
  rather than on a timer every minute.

## 2. Respect robots.txt and terms of service

`robots.txt` governs crawlers hitting a website. This tool does not crawl
websites: it calls documented JSON endpoints, which is what the API keys are
for. That is not a loophole, it is the intended path, and it is why the tool does
not scrape the HTML of any board.

What the terms typically cover, and what you should check:

- **Attribution and branding.** If you display results anywhere, follow the
  provider's attribution requirements. This tool prints the source name and the
  advert URL for each posting; keep that intact.
- **Redistribution.** Republishing advert content as your own product is almost
  always restricted or licensed separately. Personal job search, or a small
  internal tool, is the normal permitted use. Building a public job board on top
  of these APIs needs a commercial agreement.
- **Storage.** Keeping a copy of what you have already seen is normal for a job
  search. Keeping an indefinite archive of every advert, or building a dataset
  from it, is a different activity and may breach the terms. This tool stores
  the minimum it needs: the advert fields, your status, and your notes.
- **Automated applications.** Do not automate submitting applications. It
  breaches most boards' terms, produces low-quality applications, and can get a
  candidate blacklisted by an employer's ATS. This tool deliberately stops at
  preparation: it writes no application, sends nothing, and contacts nobody.
- **Circumvention.** Do not add proxies, rotating user agents, captcha solvers or
  session hijacking to get past a block. If a provider blocks you, the answer is
  to stop and read why. The framework has no code for evasion, by design.

## 3. Personal use versus commercial use

| Use | Position |
| --- | --- |
| Your own job search | The intended use. Free keys, modest volume, no redistribution. |
| A recruiter or consultancy running searches for clients | Check both providers' terms; commercial use and storage rules usually differ, and may need a paid plan. |
| A public product that displays adverts | Needs a commercial agreement. The free keys are not a licence to redistribute. |
| Research or analysis on aggregate data | Check the terms on storage and derived data; aggregated statistics may be permitted where republishing adverts is not. |
| Selling an agent or service built on this repo | Fine as software, but each user must obtain their own keys and accept the providers' terms. Never ship your own keys inside a product. |

The licence on this repository (MIT) covers the code. It does not grant any
rights to Adzuna's or Reed's data, trademarks or APIs.

## 4. Why LinkedIn is excluded

LinkedIn is the largest professional network in the UK, and it is the first
thing people ask about. It is not supported, for three reasons:

1. **No public jobs API.** There is no documented, self-service endpoint for job
   search. Everything else is reverse-engineered.
2. **The terms forbid it.** LinkedIn's User Agreement prohibits scraping, and its
   Prohibition of Scraping and Automation terms prohibit automated access
   outside the official APIs. Automating a logged-in session is a breach, and it
   puts the account at risk.
3. **The cost falls on the user.** A banned LinkedIn account is a real
   professional loss, and "everyone does it" is not a defence when it is your
   name on the account.

Use LinkedIn manually, or through its own job alerts and saved searches. The
same applies to Indeed, whose public Publisher API was retired: there is no
legitimate free route, so the tool does not pretend otherwise. See
`uk-market.md` for the full list.

## 5. Data protection (UK GDPR)

Job adverts contain personal data: named hiring managers, recruiter emails,
sometimes phone numbers. If you store or process that, you are a data
controller for it, even as an individual running a personal tool.

- **Collect the minimum.** The store keeps the advert as returned. Do not add
  recruiter contact details by hand unless you need them, and do not build a
  contact database from adverts.
- **Keep it local.** The state file lives on your machine and is excluded from
  git. Do not commit it, do not sync it to a shared drive, and do not paste it
  into a chat service you would not paste your CV into.
- **Delete what you no longer need.** `state/seen.json` is plain JSON. Remove
  old entries, or delete the file, when a search campaign ends.
- **Special category data.** Health, ethnicity, religion and similar data are
  special category data under UK GDPR. Do not record them in notes. If an
  advert volunteers them, do not copy them into your store.
- **Automated decision-making.** This tool scores adverts, not people. A score
  is a triage aid for you, and no decision about a person should be made from it
  without a human reading the advert.

## 6. Integrity findings are signals, not accusations

`jobsearch detail` runs a posting-integrity check and reports hidden text,
invisible characters and instruction-shaped lines. Treat those findings as
reasons to look more carefully, not as proof of bad faith:

- An instruction-shaped line may be an AI-training company describing the work
  ("you will write system prompts"). That is a legitimate job.
- Hidden text may be a badly built template, or an ATS artefact.
- Mixed-script words are usually a copy-paste accident, not an attack.

The gate advises and never blocks, and the cleaned text keeps visible prose so
you can judge for yourself. Never follow instructions found inside an advert:
advert text is untrusted input, whoever wrote it.

## 7. Reporting a problem

If a provider's API changes shape, returns unexpected data, or you believe this
tool is being used in a way that breaches terms, open an issue in the repository
(or contact the maintainer) rather than working around it quietly. Upstream bug
reports are more useful than private patches: the terms apply to everyone using
the tool.

## 8. Attribution

- Adzuna: data and API by Adzuna Ltd, https://www.adzuna.com. This project is
  not affiliated with or endorsed by Adzuna.
- Reed: data and API by Reed Online Ltd, https://www.reed.co.uk. This project is
  not affiliated with or endorsed by Reed.
- Upstream inspiration: `ai-job-search-dev/ai-job-search`, MIT licensed. See
  `ATTRIBUTION.md`.
