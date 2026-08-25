r"""Render the report page against a sample organization.

No GitHub access needed. Use it to check layout changes and to produce the
screenshots the Marketplace listing requires -- the listing wants a picture of
the report before any real organization has installed the app.

    .venv\Scripts\python.exe -m tools.preview_report
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from app.analysis import build_report
from app.github_client import Collaborator, Repository
from app import config

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
ago = lambda d: NOW - timedelta(days=d)

repos = [
    Repository("api", "acme/api", "private", False),
    Repository("web", "acme/web", "private", False),
    Repository("infra", "acme/infra", "private", False),
    Repository("docs", "acme/docs", "public", False),
]
grants = [
    Collaborator("jrivera-consulting", "acme/api", "write"),
    Collaborator("jrivera-consulting", "acme/infra", "admin"),
    Collaborator("designstudio-kt", "acme/web", "write"),
    Collaborator("audit-vendor-2025", "acme/api", "read"),
    Collaborator("pentest-northwind", "acme/infra", "read"),
    Collaborator("sam-contractor", "acme/web", "write"),
    Collaborator("community-helper", "acme/docs", "write"),
]
commits = {
    ("jrivera-consulting", "acme/api"): ago(412),
    ("jrivera-consulting", "acme/infra"): ago(398),
    ("designstudio-kt", "acme/web"): ago(216),
    ("sam-contractor", "acme/web"): ago(4),
    ("community-helper", "acme/docs"): ago(500),
    # audit-vendor-2025 and pentest-northwind never committed
}

report = build_report("acme", repos, grants, commits, seat_price_key="enterprise", scanned_at=NOW)

env = Environment(loader=FileSystemLoader("app/templates"))
html = env.get_template("report.html").render(
    request=None, installation_id=1, org="acme", report=report,
    flagged=report.flagged(now=NOW), stale_days=config.STALE_AFTER_DAYS,
    interval_choices=config.REVIEW_INTERVAL_CHOICES, current_interval=None,
    already_interested=False, now=NOW,
)
Path("preview.html").write_text(html, encoding="utf-8")

print(f"people: {len(report.people)}  billable: {len(report.billable_people)}")
print(f"flagged: {[p.login for p in report.flagged(now=NOW)]}")
print(f"total seats/mo: ${report.monthly_seat_cost():.2f}")
print(f"recoverable/mo: ${report.recoverable_monthly_cost(now=NOW):.2f}")
