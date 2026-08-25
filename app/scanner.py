"""Walk one installation and produce its report.

Kept apart from both the HTTP layer and the arithmetic so the network shape is
easy to see: repositories, then outside collaborators per repository, then one
commit lookup per person-repository pair that actually exists.

Rate limits are the constraint worth respecting here. An installation token
allows 5,000 requests an hour; a 50-repository organization with a handful of
contractors costs a few hundred, so the naive order is fine at the sizes this
app targets. `MAX_REPOS_PER_SCAN` keeps a pathological organization from
walking away with the whole budget.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app import config
from app.analysis import OrganizationReport, build_report
from app.github_client import Collaborator, GitHubClient, GitHubError


def scan_installation(
    client: GitHubClient,
    org: str,
    *,
    max_repos: int = config.MAX_REPOS_PER_SCAN,
) -> OrganizationReport:
    """Inventory one organization's outside-collaborator access."""

    repositories = client.repositories(limit=max_repos)

    collaborators: list[Collaborator] = []
    for repository in repositories:
        if repository.archived:
            # Nothing actionable on an archived repo, so do not spend calls.
            continue
        collaborators.extend(client.outside_collaborators(repository.full_name))

    last_commits: dict[tuple[str, str], datetime | None] = {}
    for collaborator in collaborators:
        key = (collaborator.login, collaborator.repo_full_name)
        if key in last_commits:
            continue
        try:
            last_commits[key] = client.last_commit_at(
                collaborator.repo_full_name, collaborator.login
            )
        except GitHubError:
            # A repository that cannot be read for commits is left unknown
            # rather than reported as inactive. Unknown is honest; inactive
            # would be a claim the data does not support.
            last_commits[key] = None

    return build_report(
        org,
        repositories,
        collaborators,
        last_commits,
        seat_price_key=client.seat_price_key(org),
        scanned_at=datetime.now(timezone.utc),
    )
