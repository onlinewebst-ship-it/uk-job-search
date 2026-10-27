# 02 - Application writing style

Rules for any text that will be read by a UK employer: CV bullets, cover
letters, application form answers, recruiter messages, follow-up emails. They
exist because most applications fail on the same three things: unverifiable
claims, filler language, and sentences the candidate cannot defend out loud.

Apply these rules in order. The verifiability rules outrank the style rules: a
plain sentence that is true beats a polished sentence that is not.

---

## 1. Hard bans

### No em-dashes or en-dashes

Do not use `—` (U+2014) or `–` (U+2013) anywhere in application output. Use a
full stop, a colon, a semicolon, a comma, or brackets instead. This is a house
rule, not a grammatical one: the characters are a reliable tell for
machine-written text, and some applicant tracking systems mangle them.

Rewrite like this:

| Do not write | Write |
| --- | --- |
| "I led the migration — a six-month project — across three teams." | "I led the migration, a six-month project across three teams." |
| "The role is a stretch — but one I want." | "The role is a stretch. It is one I want." |

Before sending, check the file:

```sh
grep -n '[—–]' cover-letter.md && echo "FIX THESE"
```

### Banned phrases

| Banned | Why | Use instead |
| --- | --- | --- |
| passionate about, love working with | unverifiable emotion | what you actually did, and for how long |
| results-driven, dynamic, proactive, self-starter | says nothing | the result, with its number |
| team player, collaborative by nature | everyone claims it | one example of a conflict you resolved |
| hit the ground running | cliche, and usually false | the specific thing you can start on day one |
| wear many hats | cliche | the two or three functions you actually covered |
| think outside the box | cliche | the unconventional thing you did, described plainly |
| detail-oriented | cliche, and usually contradicted by typos | a process you built that caught errors |
| proven track record | cliche | "three years running X, delivering Y" |
| excellent communication skills | unverifiable | the audience you wrote for and the outcome |
| thrive in a fast-paced environment | code for "we are understaffed" | the pace you actually worked at, described |
| excited to apply, unique opportunity, perfect fit | filler | the specific reason this role, in one sentence |
| leverage, utilise, synergise | jargon | use, with |
| responsible for | hides what you did | the verb: built, ran, negotiated, fixed |
| spearheaded | inflated | led, started, or ran |
| helped with | hides your contribution | what you owned, and what you contributed to |

### No unsupported claims

Every factual claim in an application must trace to something the candidate can
evidence: an employer, a date, a number, a named tool, a shipped artefact.

- **No invented metrics.** If the number is unknown, write `[ADD NUMBER: how
  many?]` and tell the human to fill it in. Never estimate and present it as
  fact. A wrong number in an interview is worse than a missing one.
- **No invented tools or scope.** If the CV says "used the team's Terraform
  modules", do not write "designed the Terraform estate".
- **No invented dates or titles.** Use the employer's title, not an inflated
  version. If the title was unusual, explain it in brackets, do not upgrade it.
- **No borrowed achievements.** "We" achievements can appear only as context;
  the bullet must say what the candidate personally did.
- **No claims about the employer you have not checked** (see rule 2).

If a sentence cannot survive the question "how would you prove that?", delete it
or rewrite it smaller.

---

## 2. Verify company claims before using them

Specific, accurate references to the employer are the strongest part of an
application, and a wrong one is fatal. Before writing any sentence that starts
"your..." or "as you...", check the claim against a primary source:

| Claim type | Verify with |
| --- | --- |
| Funding round, acquisition, IPO | the company's own announcement, Companies House filings, trade press |
| Product or service description | the company's live site, not a job board summary |
| Mission or strategy | the annual report or the site's about page |
| Recent news | dated articles; note the date, and prefer the last six months |
| Headcount, offices, countries | Companies House or the site's own contact page |
| Named technology stack | the advert itself, or engineering blog posts |
| Named person (hiring manager) | the advert, the company site, or a public profile; never guess a name |

Rules:

- If you cannot verify it, do not claim it. Write about the role and the work,
  which you can quote from the advert.
- Date-stamp anything time-sensitive: "your announcement in March 2026", not
  "your recent announcement".
- Never claim the company is "growing fast", "market-leading" or "innovative".
  Those are the employer's words about themselves, and repeating them reads as
  filler.
- If the only source is a job-board summary, treat it as unverified: boards
  rewrite and stale-cache employer copy.

---

## 3. The interview backtrack test

For every bullet and every paragraph, run this test:

> Read it aloud as if an interviewer has just said: "That's interesting, tell me
> more about that." Can you talk for 60 seconds with specifics: what you did,
> the scale, the constraint, the outcome, and what was yours versus the team's?

If the answer is no, the line fails. Fix it in one of three ways:

1. **Cut it.** Most failed bullets are decoration.
2. **Make it smaller and true.** "Led the migration" becomes "migrated 14
   services to Postgres over four months, owning the schema changes".
3. **Replace it with something you can defend.** A small, specific, verifiable
   example beats a large vague one every time.

Signals a line will fail the test:

- it uses an adjective where a number belongs,
- it describes a team outcome with no personal verb,
- it names a technology that does not appear in the candidate's work history,
- it claims a level ("led", "owned", "architected") that the CV dates do not
  support,
- it is longer than two lines and still does not say what changed.

---

## 4. Sentence craft

- **Active voice, past tense, first person implied.** "Rebuilt the pricing
  service", not "The pricing service was rebuilt".
- **One idea per bullet.** If it contains "and" twice, split it.
- **Lead with the verb.** No "Responsible for", no "Tasked with".
- **Put the number early.** "Cut run time from 40 minutes to 6" beats "Improved
  performance significantly".
- **Name the tool once.** Recruiters and ATS keyword matches both need the exact
  term from the advert, spelled the way the advert spells it.
- **Concrete nouns, no abstractions.** "Weekly release train" not "agile
  delivery cadence".
- **Short sentences.** If a sentence needs a comma splice to survive, split it.
- **Cut 20%.** Read the draft and delete the weakest sentence in every
  paragraph. The letter will be better.

### Bullet formula

```text
<Verb> <object> <method/tool> <result, with a number if one exists>
```

Examples:

- "Rebuilt the onboarding flow in React, cutting drop-off from 38% to 21%."
- "Ran the weekly release process for 12 engineers, reducing failed deploys
  from 4 a month to 1."
- "Wrote the pricing runbook that the support team still uses for escalations."

Weak versions of the same bullets: "Responsible for onboarding improvements",
"Helped with releases", "Created documentation".

---

## 5. UK conventions

- **CV, not resume.** Two pages maximum unless the advert or sector expects a
  long-form academic CV.
- **No photo, no date of birth, no marital status, no nationality.** These are
  not expected and can create bias or discrimination risk.
- **Right to work:** state it in one line if the advert asks, or if it explains
  a gap. Do not volunteer visa detail the employer has not asked for.
- **UK spelling:** organise, prioritise, programme (except computer program),
  centre, licence (noun) / license (verb), defence, analyse.
- **Dates:** "March 2026" or "03/2026". Avoid ambiguous numeric formats.
- **Salary:** give a range with a floor you would accept, and say whether it is
  base only. If the advert gives a range, answer inside it.
- **Notice period:** state it in weeks, and only if asked or relevant.
- **Referees:** "available on request" unless the advert asks for details now.
- **Address the letter to a named person** if the advert names one. Otherwise
  "Dear Hiring Manager". Never "To whom it may concern".
- **File format:** PDF unless the ATS asks for .docx, named
  `Firstname-Lastname-CV.pdf`, no version numbers, no "final final".

---

## 6. Cover letter shape (300-350 words)

1. **Opening (2 sentences).** The role, and the one specific reason this
   candidate, stated without adjectives. No "I am writing to apply".
2. **Evidence (2 paragraphs).** Two or three examples that map directly to the
   advert's top requirements. Same bullet formula, prose form, with numbers.
3. **The gap (1-2 sentences).** Name the one requirement the candidate does not
   meet and say what they have done that is closest, or what they are doing
   about it. Interviewers find the gap anyway; naming it first is stronger.
4. **Close (1 sentence).** Availability and a plain request for a conversation.
   No "I look forward to hearing from you at your earliest convenience".

Cut every sentence that would still be true if sent to a different employer.

---

## 7. Final checklist

Run this before anything goes out.

- [ ] No `—` or `–` anywhere (`grep -n '[—–]'` returns nothing).
- [ ] No phrase from the banned list.
- [ ] Every number traceable to a real source, and every `[ADD NUMBER]`
      placeholder filled in by the candidate.
- [ ] Every employer claim checked against a primary source, and dated if
      time-sensitive.
- [ ] Every bullet survives the backtrack test.
- [ ] Job title and company name spelled exactly as the advert spells them.
- [ ] The advert's own keywords appear, spelled the same way, at least once.
- [ ] UK spelling throughout; two pages maximum.
- [ ] Read aloud once. Anything you stumble over gets rewritten.
- [ ] A human has read it. A model cannot sign off on claims about a person's
      life.
