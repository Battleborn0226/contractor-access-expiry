"""Sync recorded installations against what GitHub actually reports.

Webhooks are a notification mechanism, not a source of truth. A delivery can
fail for reasons that have nothing to do with whether an install happened: the
machine was restarting, a deploy was mid-flight, the secret was misconfigured,
the endpoint 500'd. Every one of those produces the same outcome -- an install
that exists on GitHub and nowhere in our database.

That matters more here than in most applications, because the recorded count
*is* the experiment. The pilot is killed if fewer than ten organizations
install, so an install lost to a dropped webhook does not merely degrade a
metric -- it can end the project on evidence that was never true.

So the count is reconciled against `GET /app/installations`, which reports
current state regardless of what messages arrived. Webhooks make the record
timely; reconciliation makes it correct.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.github_client import AppClient, GitHubError
from app.storage import Storage


logger = logging.getLogger(__name__)


@dataclass
class ReconcileResult:
    added: list[str] = field(default_factory=list)
    marked_uninstalled: list[int] = field(default_factory=list)
    suspended: list[int] = field(default_factory=list)
    total_on_github: int = 0
    ran_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error: str | None = None

    @property
    def changed(self) -> bool:
        return bool(self.added or self.marked_uninstalled or self.suspended)

    def summary(self) -> str:
        if self.error:
            return f"reconcile failed: {self.error}"
        if not self.changed:
            return f"reconcile: no drift ({self.total_on_github} installation(s))"
        return (
            f"reconcile: {len(self.added)} added, "
            f"{len(self.marked_uninstalled)} marked uninstalled, "
            f"{len(self.suspended)} suspended "
            f"({self.total_on_github} on GitHub)"
        )


def reconcile(storage: Storage, client: AppClient | None = None) -> ReconcileResult:
    """Make the local record match GitHub's.

    Never raises. A reconciliation that cannot reach GitHub should leave the
    existing record alone and say so -- treating an unreachable API as "no
    installations" would wipe the very data this exists to protect.
    """

    result = ReconcileResult()

    try:
        live = (client or AppClient()).installations()
    except (GitHubError, Exception) as error:  # noqa: BLE001 - never raise
        result.error = f"{type(error).__name__}: {error}"
        logger.warning(result.summary())
        return result

    result.total_on_github = len(live)
    live_ids = {installation.id for installation in live}
    known_ids = storage.installation_ids()

    for installation in live:
        if installation.id not in known_ids:
            storage.record_installation(
                installation.id,
                installation.account_login,
                installation.account_type,
                installed_at=installation.created_at,
            )
            result.added.append(installation.account_login)

        if installation.suspended:
            # A suspended app cannot read anything, so it is not a live
            # installation for the gate's purposes.
            storage.record_uninstall(installation.id)
            result.suspended.append(installation.id)

    # Present locally but gone from GitHub: an uninstall whose webhook we
    # missed. Recorded rather than deleted, so the install still counts toward
    # the installations threshold while dropping out of "retained".
    for installation_id in storage.installation_ids(active_only=True) - live_ids:
        storage.record_uninstall(installation_id)
        result.marked_uninstalled.append(installation_id)

    logger.info(result.summary())
    return result
