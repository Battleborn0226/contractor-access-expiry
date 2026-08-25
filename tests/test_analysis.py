"""Tests for the seat-cost and staleness arithmetic.

This is the product's central claim -- an admin who catches one inflated
number stops believing the page. The cases that matter most are the ones where
a naive implementation would overstate: counting a person once per repository
instead of once per person, and billing for public or archived repositories.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.analysis import build_report
from app.github_client import Collaborator, Repository


NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def repo(name: str, visibility: str = "private", archived: bool = False) -> Repository:
    return Repository(
        name=name.split("/")[-1],
        full_name=name,
        visibility=visibility,
        archived=archived,
    )


def grant(login: str, repo_full_name: str, permission: str = "write") -> Collaborator:
    return Collaborator(
        login=login, repo_full_name=repo_full_name, permission=permission
    )


def days_ago(days: int) -> datetime:
    return NOW - timedelta(days=days)


class TestSeatCounting:
    def test_one_person_on_many_private_repos_is_one_seat(self):
        """The bug that would multiply the headline saving by repo count."""

        report = build_report(
            "acme",
            [repo("acme/api"), repo("acme/web"), repo("acme/infra")],
            [
                grant("contractor", "acme/api"),
                grant("contractor", "acme/web"),
                grant("contractor", "acme/infra"),
            ],
            {},
            seat_price_key="enterprise",
            scanned_at=NOW,
        )

        assert len(report.billable_people) == 1
        assert report.monthly_seat_cost() == pytest.approx(21.00)

    def test_public_repositories_do_not_consume_a_seat(self):
        report = build_report(
            "acme",
            [repo("acme/docs", visibility="public")],
            [grant("helper", "acme/docs")],
            {},
            seat_price_key="enterprise",
            scanned_at=NOW,
        )

        assert report.people[0].billable is False
        assert report.monthly_seat_cost() == pytest.approx(0.0)

    def test_internal_repositories_do_consume_a_seat(self):
        report = build_report(
            "acme",
            [repo("acme/shared", visibility="internal")],
            [grant("vendor", "acme/shared")],
            {},
            seat_price_key="team",
            scanned_at=NOW,
        )

        assert report.monthly_seat_cost() == pytest.approx(4.00)

    def test_a_person_with_one_private_and_one_public_repo_bills_once(self):
        report = build_report(
            "acme",
            [repo("acme/api"), repo("acme/docs", visibility="public")],
            [grant("contractor", "acme/api"), grant("contractor", "acme/docs")],
            {},
            seat_price_key="team",
            scanned_at=NOW,
        )

        assert len(report.billable_people) == 1
        assert report.monthly_seat_cost() == pytest.approx(4.00)

    def test_archived_repositories_are_excluded_entirely(self):
        """Archived repos are read-only and bill nothing -- no action to take."""

        report = build_report(
            "acme",
            [repo("acme/legacy", archived=True)],
            [grant("contractor", "acme/legacy")],
            {},
            seat_price_key="enterprise",
            scanned_at=NOW,
        )

        assert report.people == []
        assert report.monthly_seat_cost() == pytest.approx(0.0)

    def test_a_grant_on_an_unknown_repo_is_skipped(self):
        report = build_report(
            "acme", [repo("acme/api")], [grant("ghost", "acme/vanished")], {},
            scanned_at=NOW,
        )

        assert report.people == []


class TestStaleness:
    def test_a_recent_committer_is_active(self):
        report = build_report(
            "acme",
            [repo("acme/api")],
            [grant("dev", "acme/api")],
            {("dev", "acme/api"): days_ago(10)},
            scanned_at=NOW,
        )

        assert report.people[0].status(now=NOW) == "active"
        assert report.flagged(now=NOW) == []

    def test_an_old_committer_is_stale(self):
        report = build_report(
            "acme",
            [repo("acme/api")],
            [grant("dev", "acme/api")],
            {("dev", "acme/api"): days_ago(200)},
            scanned_at=NOW,
        )

        assert report.people[0].status(now=NOW) == "stale"
        assert report.people[0].days_since_commit(now=NOW) == 200

    def test_no_commits_is_its_own_state_not_stale(self):
        """Never having committed is a different signal from having stopped."""

        report = build_report(
            "acme", [repo("acme/api")], [grant("reviewer", "acme/api")], {},
            scanned_at=NOW,
        )

        assert report.people[0].status(now=NOW) == "no_commits"

    def test_the_threshold_boundary_is_inclusive(self):
        report = build_report(
            "acme",
            [repo("acme/api")],
            [grant("dev", "acme/api")],
            {("dev", "acme/api"): days_ago(90)},
            scanned_at=NOW,
        )

        assert report.people[0].status(stale_after_days=90, now=NOW) == "stale"

    def test_activity_on_any_repo_counts_for_the_person(self):
        """Someone busy on one repo is not stale because another is quiet."""

        report = build_report(
            "acme",
            [repo("acme/api"), repo("acme/web")],
            [grant("dev", "acme/api"), grant("dev", "acme/web")],
            {("dev", "acme/api"): days_ago(400), ("dev", "acme/web"): days_ago(3)},
            scanned_at=NOW,
        )

        assert report.people[0].status(now=NOW) == "active"


class TestRecoverableCost:
    def test_only_flagged_billable_people_count_toward_recoverable(self):
        report = build_report(
            "acme",
            [repo("acme/api"), repo("acme/docs", visibility="public")],
            [
                grant("busy", "acme/api"),
                grant("gone", "acme/api"),
                grant("public_only", "acme/docs"),
            ],
            {
                ("busy", "acme/api"): days_ago(5),
                ("gone", "acme/api"): days_ago(300),
            },
            seat_price_key="enterprise",
            scanned_at=NOW,
        )

        # Three people, two billable, one of those flagged.
        assert len(report.people) == 3
        assert len(report.billable_people) == 2
        assert report.monthly_seat_cost() == pytest.approx(42.00)
        assert report.recoverable_monthly_cost(now=NOW) == pytest.approx(21.00)

    def test_a_flagged_public_only_person_recovers_nothing(self):
        report = build_report(
            "acme",
            [repo("acme/docs", visibility="public")],
            [grant("gone", "acme/docs")],
            {("gone", "acme/docs"): days_ago(300)},
            seat_price_key="enterprise",
            scanned_at=NOW,
        )

        assert len(report.flagged(now=NOW)) == 1
        assert report.recoverable_monthly_cost(now=NOW) == pytest.approx(0.0)

    def test_flagged_ordering_puts_billable_and_oldest_first(self):
        report = build_report(
            "acme",
            [repo("acme/api"), repo("acme/docs", visibility="public")],
            [
                grant("cheap_and_old", "acme/docs"),
                grant("costly_recent_stale", "acme/api"),
                grant("costly_ancient", "acme/api"),
            ],
            {
                ("cheap_and_old", "acme/docs"): days_ago(900),
                ("costly_recent_stale", "acme/api"): days_ago(95),
                ("costly_ancient", "acme/api"): days_ago(500),
            },
            seat_price_key="team",
            scanned_at=NOW,
        )

        assert [person.login for person in report.flagged(now=NOW)] == [
            "costly_ancient",
            "costly_recent_stale",
            "cheap_and_old",
        ]


class TestPermissions:
    def test_the_highest_permission_across_repos_is_reported(self):
        report = build_report(
            "acme",
            [repo("acme/api"), repo("acme/web")],
            [
                grant("dev", "acme/api", permission="read"),
                grant("dev", "acme/web", permission="admin"),
            ],
            {},
            scanned_at=NOW,
        )

        assert report.people[0].highest_permission == "admin"
        assert report.people[0].repo_count == 2


class TestEmptyOrganization:
    def test_an_org_with_no_outside_collaborators_reports_zero(self):
        report = build_report("acme", [repo("acme/api")], [], {}, scanned_at=NOW)

        assert report.people == []
        assert report.monthly_seat_cost() == pytest.approx(0.0)
        assert report.recoverable_monthly_cost(now=NOW) == pytest.approx(0.0)
        assert report.flagged(now=NOW) == []
