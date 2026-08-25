"""HTTP surface: GitHub webhooks, the report page, and the two signals.

The app is read-only by design during the pilot. It holds no write scopes and
has no code path that modifies a repository, an organization, or a
collaborator. That is a product decision as much as a safety one -- an admin
evaluating an access tool is far likelier to install something that provably
cannot break their permissions, and removing a collaborator from a private
repository deletes that person's private forks irreversibly.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import logging
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import config
from app.diagnosis import diagnose
from app.github_client import GitHubClient, GitHubError
from app.reconcile import ReconcileResult, reconcile
from app.scanner import scan_installation
from app.storage import Storage


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
storage = Storage(config.DATABASE_PATH)

# Last reconciliation, surfaced on /gate so a silently failing sync is visible
# rather than something you discover at day 30.
last_reconcile: ReconcileResult | None = None


async def _reconcile_once() -> None:
    global last_reconcile
    # The GitHub client is synchronous; keep it off the event loop.
    last_reconcile = await asyncio.to_thread(reconcile, storage)


async def _reconcile_loop() -> None:
    while True:
        await asyncio.sleep(config.RECONCILE_INTERVAL_SECONDS)
        try:
            await _reconcile_once()
        except Exception:  # noqa: BLE001 - a failed sync must not kill the loop
            logger.exception("reconcile loop iteration failed")


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    # Reconcile at boot: whatever was missed while this process was down --
    # a deploy, a restart, a crash -- is picked up before serving anything.
    await _reconcile_once()
    task = asyncio.create_task(_reconcile_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Contractor Access Expiry", lifespan=lifespan)


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


def _operator_authorised(key: str, request: Request) -> bool:
    """Token from an Authorization header, or a query parameter.

    The header is preferred -- query strings end up in browser history, proxy
    logs, and referrer headers. The query form stays because the operator
    needs to open this in a browser, and these pages carry counts rather than
    collaborator identities.

    No token configured means the operator surface is off, not open.
    """

    if not config.GATE_TOKEN:
        return False

    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        if hmac.compare_digest(header.removeprefix("Bearer "), config.GATE_TOKEN):
            return True
    return bool(key) and hmac.compare_digest(key, config.GATE_TOKEN)


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


@app.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request) -> HTMLResponse:
    """Required by the Marketplace listing, and worth having regardless.

    The substantive claim -- that collaborator identities are never persisted
    -- is a property of `Storage.record_scan`, which writes counts and totals
    only. Keep it that way, or this page becomes a lie.
    """

    return templates.TemplateResponse(
        request, "privacy.html", {"updated": config.PRIVACY_UPDATED}
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
def gate(request: Request, key: str = "") -> HTMLResponse:
    """The operator's dashboard, computed from recorded events.

    Reachable so the numbers cannot be quietly reinterpreted later, but behind
    a token: it reports live counts and the conditions under which the project
    would be abandoned, which is nothing a prospective customer should find by
    guessing a URL.

    No token configured means the page is off, not open.
    """

    if not _operator_authorised(key, request):
        raise HTTPException(status_code=404, detail="not found")

    result = storage.gate()
    metrics = storage.latest_listing_metrics()

    return templates.TemplateResponse(
        request,
        "gate.html",
        {
            "gate": result,
            "days_remaining": storage.days_remaining(),
            "reconcile": last_reconcile,
            "metrics": metrics,
            "metrics_history": storage.listing_metrics_history(limit=10),
            "diagnosis": diagnose(result, metrics),
            "key": key,
        },
    )


@app.post("/gate/insights")
def record_insights(
    request: Request,
    key: str = "",
    visitors: int = Form(...),
    pageviews: int = Form(...),
    note: str = Form(""),
) -> RedirectResponse:
    """Record a reading from GitHub's Marketplace Insights page."""

    if not _operator_authorised(key, request):
        raise HTTPException(status_code=404, detail="not found")

    storage.record_listing_metrics(visitors, pageviews, note)
    return RedirectResponse(f"/gate?key={key}", status_code=303)


@app.get("/gate/backup")
def backup(request: Request, key: str = "") -> FileResponse:
    """A consistent copy of the pilot database.

    Taken through SQLite's backup API rather than by copying the file: a plain
    copy of a database being written to can be torn, and a backup that only
    sometimes restores is worse than none, because it is trusted.

    This database is the experiment. A volume failure at day twenty with no
    tested restore would erase the evidence rather than the software.
    """

    if not _operator_authorised(key, request):
        raise HTTPException(status_code=404, detail="not found")

    destination = Path(tempfile.gettempdir()) / "cae-backup.db"
    with sqlite3.connect(destination) as target:
        storage.connection.backup(target)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return FileResponse(
        destination,
        media_type="application/vnd.sqlite3",
        filename=f"cae-{stamp}.db",
    )
