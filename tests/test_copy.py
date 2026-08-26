"""The notify-me signal must not describe itself as more than it measures.

Threshold 4 counts owners who asked to hear when a paid plan launches. It was
originally worded as a question about automatic enforcement specifically, which
biased the signal toward a feature that may never be built -- a miss would then
have rejected the wrong thing. The wording was corrected before the listing went
live.

Two ways that correction could silently come undone, so both are pinned here:
the customer-facing copy drifting back to enforcement, and the operator's own
gate page labelling the result as enforcement demand or willingness to pay. The
second is the dangerous one. A mislabelled instrument does not fail loudly; it
reports a stronger conclusion than it measured, and the operator reads it off
his own dashboard on day 30 and believes it.

The `enforcement_interest` table keeps its name deliberately, so these checks
look at rendered text and prose, never at identifiers.

preview.html is deliberately absent from the list below. It is generated from
report.html by tools/preview_report.py and gitignored, so asserting on it fails
in any fresh clone -- and covering the template covers everything rendered from
it anyway.
"""

from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


def template_text(path: str) -> str:
    """Template source with Jinja expressions left in -- close enough.

    Every string under test is literal text, not interpolated, so reading the
    template beats standing up a request just to see the copy.
    """

    return (ROOT / path).read_text(encoding="utf-8")


CUSTOMER_FACING = [
    "app/templates/report.html",
    "app/templates/privacy.html",
]


@pytest.mark.parametrize("path", CUSTOMER_FACING)
def test_customer_copy_asks_about_the_paid_plan(path):
    text = template_text(path).lower()
    assert "paid plan" in text, f"{path} no longer mentions the paid plan"


@pytest.mark.parametrize("path", CUSTOMER_FACING)
def test_customer_copy_does_not_ask_about_enforcement(path):
    """Enforcement may be mentioned as one possibility, never as the offer."""

    text = template_text(path).lower()
    for phrase in (
        "tell me when enforcement ships",
        "automatic enforcement</h2>",
        "about paid enforcement",
    ):
        assert phrase not in text, f"{path} still offers enforcement: {phrase}"


def test_the_gate_does_not_label_threshold_four_as_enforcement():
    text = template_text("app/templates/gate.html").lower()
    assert "asked for paid enforcement" not in text
    assert "asked about the paid plan" in text


def test_the_gate_says_the_signal_is_not_willingness_to_pay():
    text = template_text("app/templates/gate.html").lower()
    assert "not validated willingness to pay" in text


def test_no_module_claims_the_signal_is_willingness_to_pay():
    """The claim lived in two docstrings before it lived on the page."""

    for path in ("app/storage.py", "app/main.py"):
        text = (ROOT / path).read_text(encoding="utf-8").lower()
        assert "willingness-to-pay signal" not in text, f"{path} overstates it"
