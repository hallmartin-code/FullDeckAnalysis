# Deployment

The app is live on Railway.

| | |
| --- | --- |
| **URL** | https://pitch-deck-review-production.up.railway.app |
| Project | `pitch-deck-review` (`0e5d7275-aa5c-4543-a6b0-de5dde8e7996`) |
| Service | `pitch-deck-review` (`f55d70bb-6f70-4a3a-bc95-7ac2985571d3`) |
| Environment | `production` |
| Builder | Nixpacks, Python 3.12 (pinned in `.python-version`) |
| Health check | `GET /health` |

The access code is stored in Railway's variables, not in this repo. Read it with
`railway variable list --kv`.

---

## Environment variables

Set on the Railway service. Never committed.

| Variable | Purpose |
| --- | --- |
| `ANTHROPIC_API_KEY` | Analysis provider credential |
| `DECK_REVIEW_MODEL` | `claude-opus-5` |
| `ACCESS_CODE` | Shared secret required to submit a deck |
| `RESEND_API_KEY` | Resend credential for the report emails |
| `REPORT_EMAIL_TO` | `Info@tencapital.group` |
| `REPORT_EMAIL_FROM` | `TEN Capital Deck Review <reports@tencapital.group>` |
| `PORT` | Injected by Railway; the start command reads it |

`ACCESS_CODE` matters. Without it, anyone who finds the URL can spend your Anthropic
credits — each review is a full Opus 5 call at high effort. With it set, `/api/config`
reports `access_code_required: true` and the UI shows the code field.

### Email delivery

`tencapital.group` is a verified sending domain in Resend, so the app sends from
`reports@tencapital.group` rather than the `onboarding@resend.dev` sandbox. If you
change `REPORT_EMAIL_FROM`, the new domain must be verified in the Resend dashboard
first — otherwise Resend returns HTTP 422 and the app reports it as a warning.

Check delivery of any message:

```bash
curl -H "Authorization: Bearer $RESEND_API_KEY" \
  https://api.resend.com/emails/<message-id>
```

The message id appears in the job's `email.message_id` field.

### Rotating the API keys

```bash
railway variable set "ANTHROPIC_API_KEY=sk-ant-api03-..."
railway variable set "RESEND_API_KEY=re_..."
```

Setting a variable triggers a redeploy automatically. Do the same in `.env` for local
runs.

---

## Redeploying

```bash
cd "path/to/FullDeckAnalysis"
railway up --service pitch-deck-review --ci
```

`railway up` uploads the working directory directly — no git remote required.
`.railwayignore` keeps `.env`, `output/`, `input/`, `tests/` and caches out of the
build context.

Useful commands:

```bash
railway logs                       # stream service logs
railway variable list --kv         # read current variables
railway domain status <domain-id>  # check the public URL
railway open                       # open the project dashboard
```

---

## Architecture on Railway

**Background jobs, not blocking requests.** A review takes 50–180 seconds. Holding an
HTTP request open that long is fragile through a proxy and a browser, so `POST
/api/review` returns `202` with a job id immediately and the client polls
`GET /api/jobs/{id}` every two seconds.

**Bounded concurrency.** Two worker threads, queue capped at eight. Each job spends
real money, so the pool is deliberately small; past the cap the API returns `503` with
a retry message rather than queueing without limit.

**Ephemeral storage.** Each job gets a scratch directory under the system temp dir for
the upload and the generated PDF/JSON. Uploads are deleted as soon as extraction
finishes; job directories are swept two hours after completion.

### Known limits

- **Jobs live in memory.** A redeploy or crash loses in-flight jobs, and a second
  replica would not see the first one's jobs. Fine for single-instance use; scaling
  horizontally would need Redis or a database behind `web/jobs.py`.
- **Reports expire after two hours.** Download the PDF and JSON in the session.
- **One access code for everyone.** There are no user accounts.

---

## Local development

```bash
pip install -r requirements.txt
cp .env.example .env          # add ANTHROPIC_API_KEY
python -m uvicorn web.main:app --reload --port 8000
```

Then open http://127.0.0.1:8000. Leave `ACCESS_CODE` empty locally and the gate is
skipped. The CLI still works unchanged:

```bash
python app.py input/deck.pdf
python app.py --template
```

---

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Upload page |
| `GET` | `/health` | Liveness probe |
| `GET` | `/api/config` | Accepted formats, size cap, whether a code is required |
| `POST` | `/api/review` | Upload a deck (`file`, `access_code`) → `202` + job id |
| `GET` | `/api/jobs/{id}` | Poll status; carries the full review when `done` |
| `GET` | `/api/jobs/{id}/report.pdf` | The one-page PDF |
| `GET` | `/api/jobs/{id}/report.json` | The structured analysis |
| `GET` | `/api/template.pdf` | Blank structure template |
| `GET` | `/api/docs` | OpenAPI documentation |

Errors are always `{"error": "...", "hint": "..."}`, so the client renders every
failure the same way.

```bash
# Submit and poll
JOB=$(curl -s -X POST -F "file=@deck.pdf" -F "access_code=$CODE" \
  https://pitch-deck-review-production.up.railway.app/api/review \
  | python -c "import sys,json;print(json.load(sys.stdin)['id'])")

curl -s https://pitch-deck-review-production.up.railway.app/api/jobs/$JOB

curl -o review.pdf \
  https://pitch-deck-review-production.up.railway.app/api/jobs/$JOB/report.pdf
```

---

## Note on this repository

The project sits inside a git repository rooted at `C:/Users/user/.git` — your whole
home folder. Nothing here was committed for that reason; deployment uploads the
directory directly instead. To version this project properly, initialise a repo in
the project folder itself:

```bash
cd "path/to/FullDeckAnalysis"
git init
git add -A          # .gitignore already excludes .env, output/ and input/
git commit -m "Investor presentation review app"
```

Then optionally connect it to Railway for push-to-deploy via the dashboard.
