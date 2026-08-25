"""Why the pilot failed, not merely that it did.

The four thresholds answer one question: continue or stop. That is the
decision, but it is not the lesson. A pilot that ends at three installs could
mean any of several very different things, and they imply opposite next moves:

  nobody saw the listing            -> Marketplace cannot distribute this
  people saw it and did not install -> positioning, permissions, or trust
  installs from the wrong orgs      -> the audience is not where we aimed
  right orgs, no activation         -> the product does not deliver

Only the first invalidates the premise the whole candidate was chosen on --
that an unranked listing can reach customers without promotion. The others are
fixable within it. Without the funnel numbers those cases are indistinguishable
at day 30, and the temptation is to read whichever one is most comfortable.

The visitor and pageview figures come from GitHub's Marketplace Insights,
which has no API, so they are entered by hand on the gate page.
"""

from __future__ import annotations

from dataclasses import dataclass


# Below this many listing visitors, nothing downstream can be concluded --
# the funnel was never fed.
DISCOVERY_FLOOR = 100

# Visitors who reach the listing and install. Marketplace conversion varies
# enormously; this is a floor for "the listing is not the problem", not a
# benchmark.
INSTALL_CONVERSION_FLOOR = 0.03


@dataclass(frozen=True)
class Diagnosis:
    stage: str
    headline: str
    detail: str

    @property
    def invalidates_premise(self) -> bool:
        """Whether this outcome discredits embedded-platform distribution.

        True only for a discovery failure. Everything else is a problem with
        this listing or this product, not with the strategy that produced it --
        a distinction worth keeping, because conflating them would throw away
        a filter on evidence that does not support it.
        """

        return self.stage == "discovery"


def diagnose(gate, metrics) -> Diagnosis | None:
    """Read the funnel. Returns None while there is nothing to say yet."""

    if gate is None or not gate.started:
        return None

    visitors = int(metrics["visitors"]) if metrics else 0

    if metrics is None:
        return Diagnosis(
            "unknown",
            "No listing metrics recorded",
            "Without visitor figures from Marketplace Insights, a failure at "
            "day 30 cannot be attributed. Record them at least weekly.",
        )

    if visitors < DISCOVERY_FLOOR:
        return Diagnosis(
            "discovery",
            f"Discovery failure -- {visitors} listing visitors",
            "Too few people reached the listing for anything downstream to "
            "mean much. This is the outcome that invalidates the premise: an "
            "unranked Marketplace listing did not reach customers without "
            "promotion. It says nothing about whether the product is good.",
        )

    if gate.installations == 0:
        return Diagnosis(
            "conversion",
            f"Conversion failure -- {visitors} visitors, no installs",
            "People found the listing and declined. That points at "
            "positioning, the permission set, or trust in an unknown "
            "publisher -- all fixable without abandoning the channel.",
        )

    rate = gate.installations / visitors
    if rate < INSTALL_CONVERSION_FLOOR:
        return Diagnosis(
            "conversion",
            f"Weak conversion -- {gate.installations} installs from {visitors} visitors "
            f"({rate:.1%})",
            "Discovery works; the listing does not persuade. Look at the "
            "permission screen and the first screenful of the description "
            "before blaming the channel.",
        )

    if gate.with_collaborators == 0:
        return Diagnosis(
            "audience",
            "Audience mismatch -- installs, but no outside collaborators",
            "Organizations installed and had nothing for the app to find. The "
            "listing is reaching people, but not the ones with the problem.",
        )

    if gate.activated_and_retained == 0:
        return Diagnosis(
            "product",
            "Activation failure -- qualified organizations did not engage",
            "The right organizations installed, saw their real numbers, and "
            "did not set a review interval or stay. That is the product "
            "failing to deliver, which no amount of distribution work fixes.",
        )

    return Diagnosis(
        "healthy",
        "Funnel intact",
        "Visitors are arriving, converting, and qualifying. Judge on the four "
        "thresholds.",
    )
