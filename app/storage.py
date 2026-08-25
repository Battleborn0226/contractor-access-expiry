"""Persistence, and the instrument that decides whether this project lives.

The four day-30 thresholds are computed here from recorded events rather than
assembled by hand at the end. That is deliberate. A founder measuring his own
pilot after the fact will find a way to read the numbers kindly, so the gate is
written down before the data exists and evaluated by code that does not care
about the answer.

The thresholds:
  1. at least 10 organizations installed the app
  2. at least 4 of those actually have outside collaborators
  3. at least 3 set a review interval and still had the app at day 30
  4. at least 2 asked to be told when paid enforcement ships

All four must pass. Any miss kills the project -- no extension, no promotion,
no reinterpreting the audience.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS installations (
    installation_id INTEGER PRIMARY KEY,
    account_login   TEXT NOT NULL,
    account_type    TEXT NOT NULL,
    installed_at    TEXT NOT NULL,
    uninstalled_at  TEXT,
    review_interval_days INTEGER,
    interval_set_at TEXT
);

CREATE TABLE IF NOT EXISTS scans (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    installation_id     INTEGER NOT NULL,
    org                 TEXT NOT NULL,
    repos_scanned       INTEGER NOT NULL,
    people_found        INTEGER NOT NULL,
    billable_people     INTEGER NOT NULL,
    flagged_people      INTEGER NOT NULL,
    monthly_seat_cost   REAL NOT NULL,
    recoverable_monthly REAL NOT NULL,
    scanned_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS enforcement_interest (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    installation_id INTEGER NOT NULL,
    account_login   TEXT NOT NULL,
    email           TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE(installation_id)
);

CREATE INDEX IF NOT EXISTS idx_scans_installation ON scans(installation_id, id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class GateResult:
    """Where the 30-day falsification test stands."""

    installations: int
    with_collaborators: int
    activated_and_retained: int
    enforcement_interest: int
    day: int

    THRESHOLDS = {
        "installations": 10,
        "with_collaborators": 4,
        "activated_and_retained": 3,
        "enforcement_interest": 2,
    }

    def passing(self) -> dict[str, bool]:
        return {
            "installations": self.installations >= self.THRESHOLDS["installations"],
            "with_collaborators": self.with_collaborators
            >= self.THRESHOLDS["with_collaborators"],
            "activated_and_retained": self.activated_and_retained
            >= self.THRESHOLDS["activated_and_retained"],
            "enforcement_interest": self.enforcement_interest
            >= self.THRESHOLDS["enforcement_interest"],
        }

    @property
    def verdict(self) -> str:
        if self.day < 30:
            return "running"
        return "continue" if all(self.passing().values()) else "kill"


class Storage:
    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -------------------------------------------------------- installations

    def record_installation(
        self,
        installation_id: int,
        account_login: str,
        account_type: str = "Organization",
        installed_at: datetime | None = None,
    ) -> None:
        """An install, or a reinstall of a previously removed app.

        `installed_at` exists for reconciliation. An install discovered days
        after the fact must be dated when it happened, not when it was noticed
        -- the gate counts from the earliest install, so backdating wrongly
        would shift the whole 30-day window.
        """

        when = installed_at.isoformat(timespec="seconds") if installed_at else _now()
        self.connection.execute(
            """
            INSERT INTO installations
                (installation_id, account_login, account_type, installed_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(installation_id) DO UPDATE SET
                account_login = excluded.account_login,
                uninstalled_at = NULL
            """,
            (installation_id, account_login, account_type, when),
        )
        self.connection.commit()

    def installation_ids(self, *, active_only: bool = False) -> set[int]:
        query = "SELECT installation_id FROM installations"
        if active_only:
            query += " WHERE uninstalled_at IS NULL"
        return {row["installation_id"] for row in self.connection.execute(query)}

    def record_uninstall(self, installation_id: int) -> None:
        self.connection.execute(
            "UPDATE installations SET uninstalled_at = ? WHERE installation_id = ?",
            (_now(), installation_id),
        )
        self.connection.commit()

    def set_review_interval(self, installation_id: int, days: int) -> None:
        """The activation signal: an owner chose a lease length."""

        self.connection.execute(
            """
            UPDATE installations
            SET review_interval_days = ?, interval_set_at = ?
            WHERE installation_id = ?
            """,
            (days, _now(), installation_id),
        )
        self.connection.commit()

    def installation(self, installation_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM installations WHERE installation_id = ?",
            (installation_id,),
        ).fetchone()

    # ----------------------------------------------------------------- scans

    def record_scan(self, installation_id: int, org: str, report) -> None:
        self.connection.execute(
            """
            INSERT INTO scans (
                installation_id, org, repos_scanned, people_found,
                billable_people, flagged_people, monthly_seat_cost,
                recoverable_monthly, scanned_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                installation_id,
                org,
                report.repos_scanned,
                len(report.people),
                len(report.billable_people),
                len(report.flagged()),
                report.monthly_seat_cost(),
                report.recoverable_monthly_cost(),
                report.scanned_at.isoformat(timespec="seconds"),
            ),
        )
        self.connection.commit()

    def latest_scan(self, installation_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM scans WHERE installation_id = ? ORDER BY id DESC LIMIT 1",
            (installation_id,),
        ).fetchone()

    # ---------------------------------------------------- enforcement signal

    def record_enforcement_interest(
        self, installation_id: int, account_login: str, email: str | None = None
    ) -> bool:
        """An owner asked to hear when paid enforcement ships.

        This is the willingness-to-pay signal, and the only one collected
        without charging anybody. One per installation -- an owner clicking
        twice is not two data points.
        """

        try:
            self.connection.execute(
                """
                INSERT INTO enforcement_interest
                    (installation_id, account_login, email, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (installation_id, account_login, email, _now()),
            )
        except sqlite3.IntegrityError:
            return False
        self.connection.commit()
        return True

    # ------------------------------------------------------------ the gate

    def gate(self, *, started_at: datetime | None = None, now: datetime | None = None) -> GateResult:
        """Evaluate the four day-30 thresholds against recorded events."""

        moment = now or datetime.now(timezone.utc)

        first = self.connection.execute(
            "SELECT MIN(installed_at) AS first FROM installations"
        ).fetchone()["first"]
        if started_at is None:
            started_at = (
                datetime.fromisoformat(first) if first else moment
            )
        day = max(0, (moment - started_at).days)

        installations = self.connection.execute(
            "SELECT COUNT(*) AS n FROM installations"
        ).fetchone()["n"]

        with_collaborators = self.connection.execute(
            """
            SELECT COUNT(DISTINCT installation_id) AS n FROM scans
            WHERE people_found > 0
            """
        ).fetchone()["n"]

        # Retained means still installed now, not merely installed once.
        activated_and_retained = self.connection.execute(
            """
            SELECT COUNT(*) AS n FROM installations
            WHERE review_interval_days IS NOT NULL AND uninstalled_at IS NULL
            """
        ).fetchone()["n"]

        interest = self.connection.execute(
            "SELECT COUNT(*) AS n FROM enforcement_interest"
        ).fetchone()["n"]

        return GateResult(
            installations=int(installations),
            with_collaborators=int(with_collaborators),
            activated_and_retained=int(activated_and_retained),
            enforcement_interest=int(interest),
            day=day,
        )

    def days_remaining(self, *, now: datetime | None = None) -> int:
        moment = now or datetime.now(timezone.utc)
        first = self.connection.execute(
            "SELECT MIN(installed_at) AS first FROM installations"
        ).fetchone()["first"]
        if not first:
            return 30
        elapsed = moment - datetime.fromisoformat(first)
        return max(0, (timedelta(days=30) - elapsed).days)
