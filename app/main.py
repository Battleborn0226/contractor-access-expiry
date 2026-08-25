"""HTTP surface: GitHub webhooks, the report page, and the two signals.

The app is read-only by design during the pilot. It holds no write scopes and
has no code path that modifies a repository, an organization, or a
collaborator. That is a product decision as much as a safety one -- an admin
evaluating an access tool is far likelier to install something that provably
cannot break their permissions, and removing a collaborator from a private
repository deletes that person's private forks irreversibly.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import config
from app.github_client import GitHubClient, GitHubError
from app.scanner import scan_installation
from app.storage import Storage


app = FastAPI(title="Contractor Access Expiry")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
storage = Storage(config.DATABASE_PATH)


def verify_signature(body: bytes, signature: str | None, secret: str) -> bool:
    """Constant-time check of GitHub's HMAC over the raw body.

    Compared against the exact bytes received: re-serialising the parsed JSON
    changes whitespace and key order, and the digest with it.
    """

    if not secret:
        return False
    if not signature or not signature.startswith("sha256="):
        return False

    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.removeprefix("sha256="))


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(request: Request) -> dict:
    body = await request.body()
    if not verify_signature(
        body,
        request.headers.get("X-Hub-Signature-256"),
        config.GITHUB_WEBHOOK_SECRET,
    ):
        raise HTTPException(status_code=401, detail="bad signature")

    event = request.headers.get("X-GitHub-Event", "")
    payload = await request.json()

    if event == "installation":
        action = payload.get("action")
        installation = payload.get("installation", {})
        account = installation.get("account", {})

        if action in ("created", "new_permissions_accepted", "unsuspend"):
            storage.record_installation(
                installation["id"],
                account.get("login", "unknown"),
                account.get("type", "Organization"),
            )
        elif action in ("deleted", "suspend"):
            storage.record_uninstall(installation["id"])

    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "index.html", {"stale_days": config.STALE_AFTER_DAYS}
    )


@app.get("/org/{installation_id}", response_class=HTMLResponse)
def organization(request: Request, installation_id: int) -> HTMLResponse:
    """The report page: what access exists, and what it costs."""

    record = storage.installation(installation_id)
    if record is None:
        raise HTTPException(status_code=404, detail="installation not found")

    org = record["account_login"]
    try:
        client = GitHubClient(installation_id)
        report = scan_installation(client, org)
    except GitHubError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    storage.record_scan(installation_id, org, report)

    return templates.TemplateResponse(
        request,
        "report.html",
        {
            "installation_id": installation_id,
            "org": org,
            "report": report,
            "flagged": report.flagged(),
            "stale_days": config.STALE_AFTER_DAYS,
            "interval_choices": config.REVIEW_INTERVAL_CHOICES,
            "current_interval": record["review_interval_days"],
            "already_interested": bool(
                storage.connection.execute(
                    "SELECT 1 FROM enforcement_interest WHERE installation_id = ?",
                    (installation_id,),
                ).fetchone()
            ),
            "now": datetime.now(timezone.utc),
        },
    )


@app.post("/org/{installation_id}/interval")
def set_interval(installation_id: int, days: int = Form(...)) -> RedirectResponse:
    """Activation signal: the owner chose a lease length."""

    if days not in config.REVIEW_INTERVAL_CHOICES:
        raise HTTPException(status_code=400, detail="unsupported interval")
    if storage.installation(installation_id) is None:
        raise HTTPException(status_code=404, detail="installation not found")

    storage.set_review_interval(installation_id, days)
    return RedirectResponse(f"/org/{installation_id}", status_code=303)


@app.post("/org/{installation_id}/notify-me")
def notify_me(installation_id: int, email: str = Form("")) -> RedirectResponse:
    """Willingness-to-pay signal, collected without charging anyone."""

    record = storage.installation(installation_id)
    if record is None:
        raise HTTPException(status_code=404, detail="installation not found")

    storage.record_enforcement_interest(
        installation_id, record["account_login"], email.strip() or None
    )
    return RedirectResponse(f"/org/{installation_id}", status_code=303)


@app.get("/gate", response_class=HTMLResponse)
def gate(request: Request) -> HTMLResponse:
    """The kill criteria, computed from recorded events.

    Deliberately reachable: a gate that has to be assembled by hand at the end
    is a gate that gets argued with.
    """

    return templates.TemplateResponse(
        request,
        "gate.html",
        {
            "gate": storage.gate(),
            "days_remaining": storage.days_remaining(),
        },
    )
