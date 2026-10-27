# Getting API keys

Both boards are free to register with and neither asks for a card. You need one
key set for Adzuna and one key for Reed; you only need the one for the source
you actually use.

The framework reads keys from environment variables and never from a file in
the repository. If a variable is missing, `jobsearch` exits 1 with a JSON error
that names the variable, rather than a traceback.

---

## Adzuna (free)

1. Go to **https://developer.adzuna.com/signup**
2. Register with an email address and a password. There is no approval step.
3. Sign in and open **https://developer.adzuna.com/**; your dashboard shows
   **App ID** and **App Key** under "API access details".
4. Copy both values. They are two different strings; `ADZUNA_APP_ID` is not the
   same as `ADZUNA_APP_KEY`, and swapping them produces a 401.
5. Export them in the shell you will run the tool from:

```sh
export ADZUNA_APP_ID='your-app-id'
export ADZUNA_APP_KEY='your-app-key'
```

The free tier has a daily call allowance (shown on the dashboard, and subject to
change). The tool makes one request per page of up to 50 results, so a default
25-result search is one call.

**What Adzuna gives you:** aggregated adverts from many UK boards, with salary
fields (sometimes estimated) and a truncated description. See
`docs/uk-market.md` for the coverage caveats.

---

## Reed (free)

1. Go to **https://www.reed.co.uk/developers/jobseeker**
2. Click **Register** and create a Reed account (or sign in if you have one).
3. The Jobseeker API page shows your **API key** once you are signed in. Copy
   the whole string; it is long.
4. Export it:

```sh
export REED_API_KEY='your-reed-api-key'
```

Reed authenticates with HTTP Basic auth, using the API key as the **username**
and an **empty password**. The tool builds that header for you. If you test with
`curl`, the trailing colon is essential:

```sh
curl -s -u "$REED_API_KEY:" "https://www.reed.co.uk/api/1.0/search?keywords=python&resultsToTake=1"
```

Without the colon, Reed returns 401 and it looks like the key is wrong.

**What Reed gives you:** direct employer and agency adverts, a full advert
description via `/api/1.0/jobs/{jobId}`, and structured contract and salary
fields on the detail endpoint.

---

## Verify both keys

```sh
# Adzuna: should print a JSON object with a "results" array
curl -s "https://api.adzuna.com/v1/api/jobs/gb/search/1?app_id=$ADZUNA_APP_ID&app_key=$ADZUNA_APP_KEY&what=python&results_per_page=1&content-type=application/json" | head -c 400

# Reed: should print a JSON object with "results"
curl -s -u "$REED_API_KEY:" "https://www.reed.co.uk/api/1.0/search?keywords=python&resultsToTake=1" | head -c 400
```

Then confirm the tool sees them, without writing anything to the state file:

```sh
python3 -m jobsearch search --source adzuna --query "python developer" --limit 1 --no-save
python3 -m jobsearch search --source reed   --query "python developer" --limit 1 --no-save
```

---

## Making the keys persist

Put the `export` lines at the end of your shell profile (`~/.zshrc` on macOS,
`~/.bashrc` on Linux), then open a new terminal. Do not put them in the
repository.

The tool does not read `.env` files. If you prefer to keep keys in a file, the
repository `.gitignore` already excludes `.env`, and you can load it explicitly:

```sh
set -a; . ./.env; set +a
```

Keep that file outside the repository if you can. A key committed to git is a
key you have to rotate.

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `missing_credential`, names `ADZUNA_APP_ID` | variable not exported in this shell | `export` it, or add it to your shell profile and open a new terminal |
| `missing_credential`, names `REED_API_KEY` | same | same |
| HTTP 401 from Adzuna | app id and key swapped, or a trailing space | re-copy both values; check the character count matches the dashboard (`printf %s "$ADZUNA_APP_KEY" \| wc -c`) |
| HTTP 401 from Reed | missing the blank password in Basic auth | use the tool, or `curl -u "$REED_API_KEY:"` with the colon |
| HTTP 429 | daily or per-minute allowance used up | wait, reduce `--limit`, and check the dashboard |
| HTTP 403 with an HTML body | provider blocking the request path | check the provider's terms; the tool does not attempt to evade blocks |
| Zero results for a sensible query | location too narrow, or Adzuna's `--category` too specific | drop `--location` (whole UK), remove `--category`, try a broader `--query` |

If a key has been pasted into a chat, a commit or a screenshot, rotate it in the
provider dashboard before using it again.
