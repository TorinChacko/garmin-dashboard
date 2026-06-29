# Garmin Field Log

Pulls your Garmin Connect stats daily via GitHub Actions and renders them on a
free dashboard hosted with GitHub Pages. No server, no always-on machine —
GitHub runs the fetch on a schedule and commits the new data; Pages serves
the static page that reads it.
(New added Backfill feature for past years' activities)

## How it fits together

```
login_once.py        →  run locally once, logs into Garmin, saves a token
pack_token.py         →  packs that token into one string for a GitHub Secret
scripts/fetch_garmin_data.py
                      →  runs in GitHub Actions daily, restores the token,
                         pulls stats, writes data/history.json + docs/data/history.json
docs/index.html       →  static dashboard, served by GitHub Pages from /docs,
                         reads docs/data/history.json at page load
```

## Setup (Windows)

### 1. Local environment

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Log in once, locally

```powershell
python login_once.py
```

Enter your Garmin email/password (and MFA code if prompted). This creates a
`garmin_tokens/` folder — **do not commit this folder**, it's already in
`.gitignore`.

### 3. Pack the token for GitHub

```powershell
python pack_token.py
```

This writes `garmin_tokens_b64.txt`. Open it and copy the whole contents.

### 4. Create the GitHub repo + secret

1. Push this folder to a new GitHub repo (private is fine).
2. In the repo: **Settings → Secrets and variables → Actions → New repository secret**
3. Name: `GARMIN_TOKENS_B64`
4. Value: paste the contents of `garmin_tokens_b64.txt`
5. Delete `garmin_tokens_b64.txt` from your computer afterward — it contains a
   live session token.

### 5. Enable GitHub Pages

**Settings → Pages → Source: Deploy from a branch → Branch: `main` /
folder: `/docs`** → Save.

GitHub will give you a URL like `https://<you>.github.io/<repo>/` — that's
your dashboard.

### 6. Run it

Go to the **Actions** tab → "Fetch Garmin Data" → **Run workflow** to trigger
it manually the first time. After that it runs automatically every day at
07:00 UTC (edit the `cron` line in `.github/workflows/fetch-data.yml` to
change the time).

## What gets pulled

Per day: steps, calories, distance, resting heart rate, average stress,
sleep duration, body battery charged. Add more by editing `fetch_day()` in
`scripts/fetch_garmin_data.py` — the wrapper exposes 130+ methods (see the
[library's demo.py](https://github.com/cyberjunky/python-garminconnect/blob/master/demo.py)
for the full list, e.g. `get_body_composition`, `get_training_readiness`,
`get_activities`).

## When the token expires

Garmin's refresh token is long-lived but not infinite. If the Action starts
failing with an authentication error, redo steps 2–4 (`login_once.py` →
`pack_token.py` → update the `GARMIN_TOKENS_B64` secret).

## Notes on safety

- Your Garmin password is never stored anywhere — only the OAuth token is,
  and only as an encrypted GitHub Secret (never visible in logs or to repo
  collaborators without secret access).
- If you fork or make this repo public, the workflow file and code are fine
  to share; never share the contents of `garmin_tokens_b64.txt` or paste it
  anywhere other than the GitHub Secret field.
