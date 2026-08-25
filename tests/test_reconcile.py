"""Tests for installation reconciliation.

The recorded install count is the experiment -- the pilot dies below ten -- so
these cases are about the ways a count can be silently wrong: an install whose
webhook never arrived, an uninstall we missed, a suspended app that should stop
counting as live, and an unreachable API that must not be mistaken for zero
installations.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from app.github_client import GitHubError, Installation
from app.reconcile import reconcile
from app.storage import Storage


NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store():
    with TemporaryDirectory() as directory:
        with Storage(Path(directory) / "cae.db") as storage:
            yield storage


class StubAppClient:
    """Returns a fixed installation list, or raises."""

    def __init__(self, installations=None, error: Exception | None = None) -> None:
        self._installations = installations or []
        self._error = error
        self.calls = 0

    def installations(self):
        self.calls += 1
        if self._error:
            raise self._error
        return self._installations


def install(
    id: int, login: str, *, created: datetime | None = None, suspended: bool = False
) -> Installation:
    return Installation(
        id=id,
        account_login=login,
        account_type="Organization",
        created_at=created or NOW,
        suspended=suspended,
    )


class TestMissedInstalls:
    def test_an_install_with_no_webhook_is_picked_up(self, store):
        """The failure this whole module exists for."""

        assert store.gate(now=NOW).installations == 0

        result = reconcile(store, StubAppClient([install(1, "acme")]))

        assert result.added == ["acme"]
        assert store.gate(now=NOW).installations == 1
        assert store.installation(1)["account_login"] == "acme"

    def test_a_recovered_install_is_dated_when_it_happened(self, store):
        """Backdating matters: the gate's 30 days run from the first install."""

        happened = NOW - timedelta(days=5)
        reconcile(store, StubAppClient([install(1, "acme", created=happened)]))

        recorded = store.installation(1)["installed_at"]
        assert recorded.startswith("2026-08-20")

        # The window is measured from the real date, not from discovery.
        assert store.gate(now=NOW).day == 5

    def test_reconciling_twice_adds_nothing_the_second_time(self, store):
        client = StubAppClient([install(1, "acme")])

        assert reconcile(store, client).added == ["acme"]
        assert reconcile(store, client).added == []
        assert store.gate(now=NOW).installations == 1

    def test_an_already_known_install_is_not_re_added(self, store):
        store.record_installation(1, "acme")
        result = reconcile(store, StubAppClient([install(1, "acme")]))

        assert result.added == []
        assert result.changed is False


class TestMissedUninstalls:
    def test_gone_from_github_is_marked_uninstalled(self, store):
        store.record_installation(1, "acme")
        store.set_review_interval(1, 90)
        assert store.gate(now=NOW).activated_and_retained == 1

        result = reconcile(store, StubAppClient([]))

        assert result.marked_uninstalled == [1]
        assert store.installation(1)["uninstalled_at"] is not None
        # Still counts as an install, but no longer as retained.
        assert store.gate(now=NOW).installations == 1
        assert store.gate(now=NOW).activated_and_retained == 0

    def test_a_suspended_app_stops_counting_as_retained(self, store):
        store.record_installation(1, "acme")
        store.set_review_interval(1, 90)

        result = reconcile(store, StubAppClient([install(1, "acme", suspended=True)]))

        assert result.suspended == [1]
        assert store.gate(now=NOW).activated_and_retained == 0

    def test_an_already_uninstalled_record_is_not_reprocessed(self, store):
        store.record_installation(1, "acme")
        store.record_uninstall(1)

        result = reconcile(store, StubAppClient([]))
        assert result.marked_uninstalled == []


class TestFailureHandling:
    def test_an_unreachable_api_never_wipes_the_record(self, store):
        """Treating a failed call as 'no installations' would destroy the data."""

        store.record_installation(1, "acme")
        store.record_installation(2, "globex")

        result = reconcile(store, StubAppClient(error=GitHubError("502 bad gateway")))

        assert result.error is not None
        assert result.marked_uninstalled == []
        assert store.gate(now=NOW).installations == 2
        assert store.installation(1)["uninstalled_at"] is None

    def test_reconcile_never_raises(self, store):
        result = reconcile(store, StubAppClient(error=RuntimeError("boom")))

        assert result.error is not None
        assert "RuntimeError" in result.error

    def test_the_summary_reports_what_happened(self, store):
        result = reconcile(store, StubAppClient([install(1, "acme")]))
        assert "1 added" in result.summary()

        quiet = reconcile(store, StubAppClient([install(1, "acme")]))
        assert "no drift" in quiet.summary()


class TestMixedDrift:
    def test_adds_and_removals_in_one_pass(self, store):
        store.record_installation(1, "stale-org")
        store.record_installation(2, "kept-org")

        result = reconcile(
            store,
            StubAppClient([install(2, "kept-org"), install(3, "new-org")]),
        )

        assert result.added == ["new-org"]
        assert result.marked_uninstalled == [1]
        assert result.total_on_github == 2
        assert store.gate(now=NOW).installations == 3
