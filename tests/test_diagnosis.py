"""Tests for the funnel diagnosis.

The four thresholds decide whether to continue. This decides what was learned,
and the two are easy to conflate at day 30 when the answer is disappointing.
The case that matters most is the boundary between a discovery failure -- which
discredits the whole embedded-distribution premise -- and everything else,
which is fixable inside the channel.
"""

from __future__ import annotations

import pytest

from app.diagnosis import DISCOVERY_FLOOR, diagnose
from app.storage import GateResult


def gate(
    installations=0, with_collaborators=0, retained=0, interest=0, started=True
) -> GateResult:
    return GateResult(
        installations=installations,
        with_collaborators=with_collaborators,
        activated_and_retained=retained,
        enforcement_interest=interest,
        day=30,
        started=started,
    )


def metrics(visitors: int, pageviews: int | None = None) -> dict:
    return {"visitors": visitors, "pageviews": pageviews or visitors * 2}


class TestNothingToSayYet:
    def test_an_unstarted_pilot_is_not_diagnosed(self):
        assert diagnose(gate(started=False), metrics(500)) is None

    def test_no_gate_at_all(self):
        assert diagnose(None, metrics(500)) is None

    def test_missing_metrics_says_so_rather_than_guessing(self):
        result = diagnose(gate(), None)
        assert result.stage == "unknown"
        assert "cannot be attributed" in result.detail


class TestDiscoveryFailure:
    def test_too_few_visitors_is_a_discovery_failure(self):
        result = diagnose(gate(), metrics(12))
        assert result.stage == "discovery"
        assert "12 listing visitors" in result.headline

    def test_only_discovery_failure_invalidates_the_premise(self):
        """The distinction the whole module exists to preserve."""

        assert diagnose(gate(), metrics(12)).invalidates_premise is True
        assert diagnose(gate(), metrics(500)).invalidates_premise is False

    def test_the_floor_is_exclusive(self):
        assert diagnose(gate(), metrics(DISCOVERY_FLOOR - 1)).stage == "discovery"
        assert diagnose(gate(), metrics(DISCOVERY_FLOOR)).stage != "discovery"

    def test_a_discovery_failure_says_nothing_about_the_product(self):
        result = diagnose(gate(), metrics(5))
        assert "says nothing about whether the product is good" in result.detail


class TestConversionFailure:
    def test_visitors_but_no_installs(self):
        result = diagnose(gate(installations=0), metrics(800))
        assert result.stage == "conversion"
        assert "no installs" in result.headline
        assert "permission set" in result.detail or "permission" in result.detail

    def test_weak_conversion_is_still_a_conversion_problem(self):
        result = diagnose(gate(installations=2), metrics(1000))
        assert result.stage == "conversion"
        assert "0.2%" in result.headline

    def test_healthy_conversion_moves_past_this_stage(self):
        result = diagnose(gate(installations=50, with_collaborators=10), metrics(500))
        assert result.stage != "conversion"


class TestAudienceAndProduct:
    def test_installs_without_collaborators_is_an_audience_mismatch(self):
        result = diagnose(gate(installations=40, with_collaborators=0), metrics(500))
        assert result.stage == "audience"
        assert "nothing for the app to find" in result.detail

    def test_qualified_installs_without_activation_is_the_product(self):
        result = diagnose(
            gate(installations=40, with_collaborators=20, retained=0), metrics(500)
        )
        assert result.stage == "product"
        assert "no amount of distribution work fixes" in result.detail

    def test_a_working_funnel_defers_to_the_thresholds(self):
        result = diagnose(
            gate(installations=40, with_collaborators=20, retained=8), metrics(500)
        )
        assert result.stage == "healthy"
        assert result.invalidates_premise is False


class TestOrdering:
    def test_discovery_is_judged_before_conversion(self):
        """A starved funnel must not be read as a conversion problem."""

        result = diagnose(gate(installations=0), metrics(3))
        assert result.stage == "discovery"

    def test_conversion_is_judged_before_audience(self):
        result = diagnose(gate(installations=1, with_collaborators=0), metrics(900))
        assert result.stage == "conversion"
