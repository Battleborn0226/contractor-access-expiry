"""Settings for Contractor Access Expiry."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


# GitHub App credentials. Created by Max in the GitHub UI; the private key is
# never committed. See README for the registration steps.
GITHUB_APP_ID = _env("GITHUB_APP_ID")
GITHUB_PRIVATE_KEY = _env("GITHUB_PRIVATE_KEY")
GITHUB_PRIVATE_KEY_PATH = _env("GITHUB_PRIVATE_KEY_PATH")
GITHUB_WEBHOOK_SECRET = _env("GITHUB_WEBHOOK_SECRET")

GITHUB_API = "https://api.github.com"

DATABASE_PATH = _env("CAE_DB") or str(Path("data") / "cae.db")

# A grant with no commit activity for this many days is surfaced for review.
# Deliberately conservative: absence of commits is not proof of inactivity,
# so the threshold is long enough that a flagged grant is worth a human look.
STALE_AFTER_DAYS = 90

# Default lease length offered at install.
DEFAULT_REVIEW_INTERVAL_DAYS = 90
REVIEW_INTERVAL_CHOICES = (30, 60, 90)

# Published GitHub per-user list prices, USD/month, as of 2026-08.
# Estimates shown to the customer, never billed on. Labelled as such in the UI
# because an inflated saving is the fastest way to lose an admin's trust.
SEAT_PRICE_USD = {
    "free": 0.00,
    "team": 4.00,
    "enterprise": 21.00,
}
DEFAULT_SEAT_PRICE_KEY = "team"

# Repository visibilities that consume a licensed seat for an outside
# collaborator. Public repos do not.
BILLABLE_VISIBILITIES = ("private", "internal")

# Rate-limit courtesy: cap how many repos one scan will walk.
MAX_REPOS_PER_SCAN = 500

# How often to re-sync installations against GitHub. Six hours is far more
# often than needed for correctness -- boot-time reconciliation already covers
# restarts -- but cheap enough (one API call) that drift never lasts a day.
RECONCILE_INTERVAL_SECONDS = 6 * 60 * 60


@dataclass(frozen=True)
class Settings:
    app_id: str = GITHUB_APP_ID
    webhook_secret: str = GITHUB_WEBHOOK_SECRET
    database_path: str = DATABASE_PATH
    stale_after_days: int = STALE_AFTER_DAYS

    def private_key(self) -> str:
        """The App's PEM, from the environment or a file beside the app."""

        if GITHUB_PRIVATE_KEY:
            return GITHUB_PRIVATE_KEY.replace("\\n", "\n")
        if GITHUB_PRIVATE_KEY_PATH:
            return Path(GITHUB_PRIVATE_KEY_PATH).read_text(encoding="utf-8")
        raise RuntimeError(
            "No GitHub App private key: set GITHUB_PRIVATE_KEY or "
            "GITHUB_PRIVATE_KEY_PATH"
        )
