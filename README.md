# Contractor Access Expiry

A read-only GitHub App that finds outside collaborators whose access nobody
intended to still exist, and shows what those licensed seats cost you.

**[Install from GitHub →](https://github.com/apps/contractor-access-expiry)**

## Your IdP offboards employees. Nobody offboards contractors.

GitHub's SCIM provisioning manages organization **members**. Outside
collaborators are not members, so they sit outside that lifecycle entirely.
When a contract ends, the identity provider does nothing — the repository grant
just stays.

And on private or internal repositories, each outside collaborator consumes a
licensed seat for as long as that grant exists. An organization carrying five
forgotten contractors on Enterprise is paying roughly **$105 a month** for
access that ended months ago.

*Enterprise Managed Users organizations using the guest-collaborator role are
an exception — those accounts are IdP-provisioned, so this gap is already
closed for you.*

## What you get

Install it, and it shows you:

- every outside collaborator across your repositories, and what they can reach
- which of them have had no commit activity in 90+ days
- what those licensed seats are costing per month
- a review interval for new grants, so this does not build up again

## What it cannot do

It holds **no write permissions**. There is no code path that adds, changes, or
removes access — the capability does not exist in the app, not merely in the
current configuration.

That is deliberate. Removing an outside collaborator from a private repository
permanently deletes any private forks they hold, and nothing can undo it. That
decision stays with you, made in GitHub.

The permissions requested are Metadata, Contents, and Administration
(read-only), plus organization Members (read-only). The source is right here if
you would rather check than take our word for it.

## How the numbers are worked out

Two rules, because an inflated figure is worse than no figure:

**Seats count once per person, not once per grant.** GitHub bills one licensed
seat for an outside collaborator however many private repositories they can
reach. Counting grants instead would multiply the headline number by your repo
count and make it fiction.

**"No commit activity" means exactly that.** A contractor who only reviews pull
requests leaves no commit trail, so nothing here concludes that a person is
inactive. Flagged entries are a list worth checking, never a list to delete.

Seat costs are estimated from GitHub's published per-user rates and are
labelled as estimates. Your invoice is the authority.

---

Running or deploying this yourself: see [DEPLOY.md](DEPLOY.md).
