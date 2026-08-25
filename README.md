# Contractor Access Expiry

A read-only GitHub App that finds outside collaborators whose access nobody
intended to still exist, and shows what those licensed seats cost per month.

This is a **30-day falsification test**, not a product launch. What is being
tested is not whether the tool is useful — it is whether GitHub Marketplace
delivers installs to an unranked listing with no promotion at all. That premise
underpins the whole business case and has never been verified.

## The pitch

> Your identity provider offboards employees. Nobody offboards contractors.

GitHub's SCIM provisioning manages organization **members**. Outside
collaborators are not members, so they sit outside that lifecycle entirely —
and on private or internal repositories each one consumes a licensed seat for
as long as the grant exists. An organization carrying five forgotten
contractors on Enterprise is paying roughly $105 a month for access that ended
months ago.

That framing matters. Selling security hygiene means asking someone to pay for
the absence of a bad thing. Selling cost recovery means pointing at their own
invoice.

**Exception:** Enterprise Managed Users has a guest-collaborator role that *is*
IdP-provisioned. EMU organizations have solved this upstream and are not the
target. Do not market this as a universal Enterprise gap.

## What it does, and does not

Inventories outside collaborators across all visible repositories, flags those
with no commit activity in 90+ days, estimates the monthly seat cost, and lets
an owner set a review interval for new grants.

It holds **no write permissions**. There is no code path that adds, changes, or
removes access. This is a product decision as much as a safety one: an admin
evaluating an access tool is far likelier to install something that provably
cannot break their permissions. It also avoids a genuine trap — removing an
outside collaborator from a private repository permanently deletes any private
forks they hold, and that is not reversible by anyone.

## Two rules the arithmetic follows

**Seats are counted per person, not per grant.** GitHub bills one licensed seat
for an outside collaborator no matter how many private repositories they can
reach. Counting grants would multiply the headline saving by repo count and
make the number fiction. `tests/test_analysis.py` pins this.

**Absence of commits is reported as absence of commits.** A contractor who only
reviews pull requests leaves no commit trail. Nothing here concludes that a
person is inactive — `no_commits` is a distinct state from `stale`, and the
report says "worth checking", never "safe to delete".

Both rules exist because an admin who catches one inflated figure stops
believing the rest of the page.

## Layout

| | |
|---|---|
| `app/github_client.py` | App JWT auth, installation tokens, the read calls |
| `app/analysis.py` | seat cost and staleness — the central claim |
| `app/scanner.py` | walks one installation |
| `app/storage.py` | persistence, and the 30-day gate |
| `app/main.py` | webhook, report page, the two signals |
| `tools/preview_report.py` | renders a sample report with no GitHub access |

## The gate

Four thresholds, written down before the data existed, evaluated by
`Storage.gate()` and visible at `/gate`. A founder measuring his own pilot
after the fact will read the numbers kindly, so the gate is computed from
recorded events by code that does not care about the answer.

| Threshold | Target |
|---|---|
| Organizations installed | 10 |
| ...with outside collaborators | 4 |
| ...that set a review interval and kept the app | 3 |
| ...that asked for paid enforcement | 2 |

**All four must pass at day 30.** Any miss kills it — no extension, no
promotion, no reinterpreting the audience. Ten installs a month is the minimum
trajectory to clear GitHub's 100-install requirement before a paid plan is even
permitted.

## Tests

```bash
.venv\Scripts\python.exe -m pytest tests/ -q
```

37 tests, no network. The analysis suite covers the cases where a naive
implementation would overstate the saving — one person across many private
repos, public-only collaborators, archived repositories.

