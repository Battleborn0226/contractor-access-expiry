"""Tests for persistence and the kill-criteria gate.

The gate is the instrument that decides whether Max keeps going, so its
counting rules matter more than they look. The cases below pin the ones that
would otherwise drift toward a flattering answer: an uninstalled org must not
count as retained, a duplicate click must not count twice, and an org that
installed but has no outside collaborators must not count as demand.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from app.analysis import build_report
from app.github_client import Collaborator, Repository
from app.storage import Storage


NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store():
    with TemporaryDirectory() as directory:
        with Storage(Path(directory) / "cae.db") as storage:
            yield storage


def simple_report(people: int = 1):
    repos = [Repository("api", "acme/api", "private", False)]
    grants = [
        Collaborator(f"person{index}", "acme/api", "write")
        for index in range(people)
    ]
    return build_report("acme", repos, grants, {}, seat_price_key="team", scanned_at=NOW)


class TestInstallations:
    def test_install_then_uninstall_is_tracked(self, store):
        store.record_installation(1, "acme")
        assert store.installation(1)["uninstalled_at"] is None

        store.record_uninstall(1)
        assert store.installation(1)["uninstalled_at"] is not None

    def test_reinstalling_clears_the_uninstall_marker(self, store):
        store.record_installation(1, "acme")
        store.record_uninstall(1)
        store.record_installation(1, "acme")

        assert store.installation(1)["uninstalled_at"] is None

    def test_setting_a_review_interval_is_recorded(self, store):
        store.record_installation(1, "acme")
        store.set_review_interval(1, 60)

        record = store.installation(1)
        assert record["review_interval_days"] == 60
        assert record["interval_set_at"] is not None


class TestEnforcementInterest:
    def test_interest_is_recorded_once_per_installation(self, store):
        store.record_installation(1, "acme")

        assert store.record_enforcement_interest(1, "acme", "a@acme.com") is True
        assert store.record_enforcement_interest(1, "acme", "a@acme.com") is False

        assert store.gate(now=NOW).enforcement_interest == 1


class TestGate:
    def test_a_fresh_pilot_fails_everything(self, store):
        result = store.gate(now=NOW)

        assert result.installations == 0
        assert not any(result.passing().values())

    def test_an_org_with_no_collaborators_does_not_count_as_demand(self, store):
        store.record_installation(1, "acme")
        store.record_scan(1, "acme", simple_report(people=0))

        result = store.gate(now=NOW)
        assert result.installations == 1
        assert result.with_collaborators == 0

    def test_an_org_with_collaborators_counts(self, store):
        store.record_installation(1, "acme")
        store.record_scan(1, "acme", simple_report(people=2))

        assert store.gate(now=NOW).with_collaborators == 1

    def test_rescanning_does_not_double_count_an_org(self, store):
        store.record_installation(1, "acme")
        store.record_scan(1, "acme", simple_report(people=2))
        store.record_scan(1, "acme", simple_report(people=2))

        assert store.gate(now=NOW).with_collaborators == 1

    def test_an_uninstalled_org_is_not_retained(self, store):
        """Activation plus churn is not activation."""

        store.record_installation(1, "acme")
        store.set_review_interval(1, 90)
        assert store.gate(now=NOW).activated_and_retained == 1

        store.record_uninstall(1)
        assert store.gate(now=NOW).activated_and_retained == 0

    def test_verdict_is_running_before_day_thirty(self, store):
        store.record_installation(1, "acme")
        assert store.gate(now=NOW).verdict == "running"

    def test_a_passing_pilot_says_continue(self, store):
        started = NOW - timedelta(days=31)

        for index in range(10):
            store.record_installation(index, f"org{index}")
        for index in range(4):
            store.record_scan(index, f"org{index}", simple_report(people=1))
        for index in range(3):
            store.set_review_interval(index, 90)
        for index in range(2):
            store.record_enforcement_interest(index, f"org{index}")

        result = store.gate(started_at=started, now=NOW)
        assert all(result.passing().values())
        assert result.verdict == "continue"

    def test_missing_one_threshold_kills_it(self, store):
        started = NOW - timedelta(days=31)

        for index in range(10):
            store.record_installation(index, f"org{index}")
        for index in range(4):
            store.record_scan(index, f"org{index}", simple_report(people=1))
        for index in range(3):
            store.set_review_interval(index, 90)
        # Only one enforcement signal; the threshold is two.
        store.record_enforcement_interest(0, "org0")

        result = store.gate(started_at=started, now=NOW)
        assert result.passing()["enforcement_interest"] is False
        assert result.verdict == "kill"

    def test_days_remaining_counts_down_from_first_install(self, store):
        store.record_installation(1, "acme")
        remaining = store.days_remaining(now=NOW + timedelta(days=10))
        assert 19 <= remaining <= 20
