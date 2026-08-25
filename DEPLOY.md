# Running your own instance

The hosted app is at <https://contractor-access-expiry.fly.dev>. These are the
steps to run your own.

## 1. Register a GitHub App

Settings → Developer settings → GitHub Apps → New GitHub App.

- **Webhook URL**: `https://<your-host>/webhook`
- **Webhook secret**: generate a long random string and keep it
- **Repository permissions**: Metadata, Contents, Administration — all
  `Read-only`
- **Organization permissions**: Members `Read-only`
- **Subscribe to events**: Installation

Request no write permission. The application has no code path that writes, and
the permission screen is the first thing a cautious admin reads.

Then generate a private key on the App's page. It downloads once and GitHub
never shows it again. Keep it outside the repository and out of any folder
that syncs to cloud storage.

## 2. Configure

Four environment variables:

```
GITHUB_APP_ID=<numeric app id>
GITHUB_PRIVATE_KEY_PATH=/path/to/your/private-key.pem
GITHUB_WEBHOOK_SECRET=<the secret from step 1>
CAE_DB=data/cae.db
```

`GITHUB_PRIVATE_KEY` may hold the PEM contents directly instead of a path,
which is what a platform secret store usually wants.

## 3. Run

Locally:

```bash
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The repository includes a `Dockerfile` and a `fly.toml`. Any host giving you a
stable HTTPS URL will do; the app is a single process over a SQLite file and
needs no managed database at this scale.

**The volume is not optional.** `fly.toml` mounts one at `/data` because the
SQLite file holds all recorded state. On an ephemeral filesystem it resets
silently on every deploy, and nothing tells you it happened.

```bash
fly volumes create cae_data --size 1 --region iad
fly secrets set GITHUB_APP_ID=... GITHUB_WEBHOOK_SECRET=...
fly deploy
```

Check `https://<your-host>/healthz` returns `{"status":"ok"}`.

## 4. Point the webhook at it

Back on the App's settings page, set the webhook URL to `https://<your-host>/webhook`,
tick Active, and paste the same secret you configured. Under Permissions &
events, subscribe to **Installation**.

Content type must be `application/json`. It defaults to
`x-www-form-urlencoded`, which the endpoint cannot parse — the resulting
failures look like signature errors.

Installation state is also reconciled against the GitHub API at start-up and
every six hours, so a missed delivery corrects itself. The webhook governs how
quickly a new installation appears, not whether it is recorded at all.

## Operator dashboard

`/gate` reports installation counts and sync status. It requires
`GATE_TOKEN` to be set and matched as a query parameter
(`/gate?key=<token>`); with no token configured the page returns 404 rather
than serving openly.

## Tests

```bash
.venv\Scripts\python.exe -m pytest tests/ -q
```

No network access required. The analysis suite covers the cases where a naive
implementation would overstate the saving: one person across many private
repositories, collaborators on public repositories only, and archived
repositories.

## Layout

| | |
|---|---|
| `app/github_client.py` | App JWT auth, installation tokens, the read calls |
| `app/analysis.py` | seat cost and staleness — the central claim |
| `app/scanner.py` | walks one installation |
| `app/reconcile.py` | syncs installations against GitHub |
| `app/storage.py` | persistence |
| `app/main.py` | webhook, report page, privacy page |
| `tools/preview_report.py` | renders a sample report with no GitHub access |
| `tools/make_listing_art.py` | generates listing imagery |
