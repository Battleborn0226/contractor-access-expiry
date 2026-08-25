"""Turn an organization's outside-collaborator grants into a reviewable report.

Two numbers matter to an admin: who still has access that nobody intended, and
what it is costing. Both are easy to overstate, and overstating either is the
fastest way to lose the reader -- an admin who catches one inflated figure will
not trust the rest of the page.

So two rules run through this module:

  Seats are deduplicated by person, not counted per grant. GitHub bills one
  licensed seat for an outside collaborator regardless of how many private
  repositories they can reach. Counting grants would multiply the "saving" by
  the number of repos and make the headline number fiction.

  Absence of commits is reported as absence of commits. A contractor who only
  reviews pull requests leaves no commit trail, so nothing here concludes that
  a person is inactive -- it reports what was observed and lets a human judge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app import config
from app.github_client import Collaborator, Repository


@dataclass(frozen=True)
class Grant:
    """One person's access to one repository."""

    login: str
    repo_full_name: str
    permission: str
    billable: bool
    last_commit_at: datetime | None

    def days_since_commit(self, *, now: datetime | None = None) -> int | None:
        if self.last_commit_at is None:
            return None
        moment = now or datetime.now(timezone.utc)
        return (moment - self.last_commit_at).days


@dataclass
class PersonSummary:
    """Everything one outside collaborator can reach in this organization."""

    login: str
    grants: list[Grant] = field(default_factory=list)

    @property
    def billable(self) -> bool:
        """Whether this person consumes a licensed seat.

        One seat per person, however many private repositories they hold.
        """

        return any(grant.billable for grant in self.grants)

    @property
    def repo_count(self) -> int:
        return len(self.grants)

    @property
    def highest_permission(self) -> str:
        order = ["read", "triage", "write", "maintain", "admin"]
        return max(
            (grant.permission for grant in self.grants),
            key=lambda permission: order.index(permission)
            if permission in order
            else 0,
            default="read",
        )

    @property
    def last_commit_at(self) -> datetime | None:
        seen = [g.last_commit_at for g in self.grants if g.last_commit_at]
        return max(seen) if seen else None

    def days_since_commit(self, *, now: datetime | None = None) -> int | None:
        if self.last_commit_at is None:
            return None
        moment = now or datetime.now(timezone.utc)
        return (moment - self.last_commit_at).days

    def status(
        self,
        *,
        stale_after_days: int = config.STALE_AFTER_DAYS,
        now: datetime | None = None,
    ) -> str:
        """`active`, `stale`, or `no_commits` -- never `inactive`.

        `no_commits` is a separate state from `stale` on purpose. It is the
        stronger signal (this person has never committed here at all) but also
        the one most likely to have an innocent explanation, so it is labelled
        distinctly rather than folded into staleness.
        """

        days = self.days_since_commit(now=now)
        if days is None:
            return "no_commits"
        return "stale" if days >= stale_after_days else "active"


@dataclass
class OrganizationReport:
    org: str
    seat_price_key: str
    people: list[PersonSummary]
    repos_scanned: int
    scanned_at: datetime

    @property
    def seat_price(self) -> float:
        return config.SEAT_PRICE_USD.get(
            self.seat_price_key, config.SEAT_PRICE_USD[config.DEFAULT_SEAT_PRICE_KEY]
        )

    @property
    def billable_people(self) -> list[PersonSummary]:
        return [person for person in self.people if person.billable]

    def flagged(
        self,
        *,
        stale_after_days: int = config.STALE_AFTER_DAYS,
        now: datetime | None = None,
    ) -> list[PersonSummary]:
        """People worth a human look, worst first."""

        candidates = [
            person
            for person in self.people
            if person.status(stale_after_days=stale_after_days, now=now)
            in ("stale", "no_commits")
        ]
        return sorted(
            candidates,
            key=lambda person: (
                not person.billable,
                -(person.days_since_commit(now=now) or 10**6),
            ),
        )

    def monthly_seat_cost(self) -> float:
        """What the organization's outside collaborators cost per month."""

        return len(self.billable_people) * self.seat_price

    def recoverable_monthly_cost(
        self,
        *,
        stale_after_days: int = config.STALE_AFTER_DAYS,
        now: datetime | None = None,
    ) -> float:
        """The part of that attached to people with no recent commit activity.

        Called *recoverable* rather than *wasted*: whether any of it should
        actually be reclaimed is the admin's judgement, not this program's.
        """

        flagged = [
            person
            for person in self.flagged(stale_after_days=stale_after_days, now=now)
            if person.billable
        ]
        return len(flagged) * self.seat_price


def build_report(
    org: str,
    repositories: list[Repository],
    collaborators: list[Collaborator],
    last_commits: dict[tuple[str, str], datetime | None],
    *,
    seat_price_key: str = config.DEFAULT_SEAT_PRICE_KEY,
    scanned_at: datetime | None = None,
) -> OrganizationReport:
    """Assemble the report from raw GitHub reads.

    Kept free of network calls so the whole scoring path is testable against
    fixtures -- the arithmetic in here is the product's central claim, and it
    should not require a live organization to verify.
    """

    visibility = {repo.full_name: repo for repo in repositories}
    people: dict[str, PersonSummary] = {}

    for collaborator in collaborators:
        repo = visibility.get(collaborator.repo_full_name)
        if repo is None or repo.archived:
            # An archived repo is read-only for everyone; it neither poses the
            # access risk nor bills a seat, so it does not belong in a report
            # that asks the admin to act.
            continue

        person = people.setdefault(
            collaborator.login, PersonSummary(login=collaborator.login)
        )
        person.grants.append(
            Grant(
                login=collaborator.login,
                repo_full_name=collaborator.repo_full_name,
                permission=collaborator.permission,
                billable=repo.billable,
                last_commit_at=last_commits.get(
                    (collaborator.login, collaborator.repo_full_name)
                ),
            )
        )

    return OrganizationReport(
        org=org,
        seat_price_key=seat_price_key,
        people=sorted(people.values(), key=lambda person: person.login.lower()),
        repos_scanned=len(repositories),
        scanned_at=scanned_at or datetime.now(timezone.utc),
    )
