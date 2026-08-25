"""GitHub App authentication and the handful of REST calls this app makes.

A GitHub App authenticates in two steps: sign a short-lived JWT with the App's
private key to prove who the App is, then exchange it for an installation
access token scoped to one organization. Installation tokens last an hour, so
they are cached until shortly before expiry rather than minted per request.

Only read scopes are used. Nothing here can modify an organization -- the
write path does not exist yet by design, and the Marketplace listing says so.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import httpx
import jwt

from app import config


class GitHubError(RuntimeError):
    """A GitHub API call failed in a way the caller should see."""


@dataclass(frozen=True)
class Repository:
    name: str
    full_name: str
    visibility: str
    archived: bool

    @property
    def billable(self) -> bool:
        """Whether an outside collaborator here consumes a licensed seat."""

        return self.visibility in config.BILLABLE_VISIBILITIES


@dataclass(frozen=True)
class Collaborator:
    login: str
    repo_full_name: str
    permission: str


def build_app_jwt(app_id: str, private_key: str, *, now: int | None = None) -> str:
    """Sign the App-level JWT GitHub accepts for installation endpoints.

    `iat` is backdated 60 seconds because GitHub rejects tokens whose issue
    time is ahead of its own clock, and small drift is common.
    """

    issued_at = int(now if now is not None else time.time())
    payload = {
        "iat": issued_at - 60,
        "exp": issued_at + 540,  # 9 minutes; GitHub's ceiling is 10
        "iss": app_id,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


@dataclass(frozen=True)
class Installation:
    """One organization's installation, as GitHub reports it."""

    id: int
    account_login: str
    account_type: str
    created_at: datetime | None
    suspended: bool


class AppClient:
    """App-level GitHub access, authenticated with the App JWT alone.

    Separate from `GitHubClient` because these endpoints are about the app
    itself rather than any one installation, and they are reached with the
    signed JWT rather than an installation token.
    """

    def __init__(
        self,
        *,
        app_id: str | None = None,
        private_key: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.app_id = app_id or config.GITHUB_APP_ID
        self._private_key = private_key
        self._client = client or httpx.Client(timeout=30.0)

    def _key(self) -> str:
        if self._private_key is not None:
            return self._private_key
        return config.Settings().private_key()

    def installations(self) -> list[Installation]:
        """Every organization that currently has this app installed.

        This is ground truth. Webhook deliveries can fail -- a restart, a
        deploy, a momentary outage -- and an install recorded nowhere is an
        install that never counted. Asking GitHub directly does not depend on
        having successfully received a message at the moment it was sent.
        """

        found: list[Installation] = []
        page = 1
        while True:
            response = self._client.get(
                f"{config.GITHUB_API}/app/installations",
                params={"per_page": 100, "page": page},
                headers={
                    "Authorization": f"Bearer {build_app_jwt(self.app_id, self._key())}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            if response.status_code != 200:
                raise GitHubError(
                    f"could not list installations ({response.status_code}): "
                    f"{response.text[:200]}"
                )

            batch = response.json()
            if not batch:
                return found

            for item in batch:
                account = item.get("account") or {}
                created = item.get("created_at")
                found.append(
                    Installation(
                        id=int(item["id"]),
                        account_login=account.get("login", "unknown"),
                        account_type=account.get("type", "Organization"),
                        created_at=(
                            datetime.fromisoformat(created.replace("Z", "+00:00"))
                            if created
                            else None
                        ),
                        suspended=bool(item.get("suspended_at")),
                    )
                )

            if len(batch) < 100:
                return found
            page += 1


class GitHubClient:
    """Read-only GitHub access for one installation."""

    def __init__(
        self,
        installation_id: int,
        *,
        app_id: str | None = None,
        private_key: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.installation_id = installation_id
        self.app_id = app_id or config.GITHUB_APP_ID
        self._private_key = private_key
        self._client = client or httpx.Client(timeout=30.0)
        self._token: str | None = None
        self._token_expires: datetime | None = None

    # ------------------------------------------------------------------ auth

    def _key(self) -> str:
        if self._private_key is not None:
            return self._private_key
        return config.Settings().private_key()

    def _installation_token(self) -> str:
        if self._token and self._token_expires:
            # Refresh a minute early rather than racing the expiry.
            if datetime.now(timezone.utc) < self._token_expires - timedelta(minutes=1):
                return self._token

        app_jwt = build_app_jwt(self.app_id, self._key())
        response = self._client.post(
            f"{config.GITHUB_API}/app/installations/"
            f"{self.installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        if response.status_code != 201:
            raise GitHubError(
                f"could not mint installation token "
                f"({response.status_code}): {response.text[:200]}"
            )

        body = response.json()
        self._token = body["token"]
        self._token_expires = datetime.fromisoformat(
            body["expires_at"].replace("Z", "+00:00")
        )
        return self._token

    # ------------------------------------------------------------- transport

    def _get(self, path: str, **params: Any) -> httpx.Response:
        return self._client.get(
            f"{config.GITHUB_API}{path}",
            params=params or None,
            headers={
                "Authorization": f"Bearer {self._installation_token()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def _paginate(self, path: str, *, limit: int | None = None, **params: Any) -> Iterator[dict]:
        """Walk a paginated list endpoint, yielding items."""

        page = 1
        seen = 0
        while True:
            response = self._get(path, per_page=100, page=page, **params)
            if response.status_code == 404:
                return
            if response.status_code != 200:
                raise GitHubError(
                    f"GET {path} failed ({response.status_code}): "
                    f"{response.text[:200]}"
                )

            batch = response.json()
            if not batch:
                return
            for item in batch:
                yield item
                seen += 1
                if limit is not None and seen >= limit:
                    return
            if len(batch) < 100:
                return
            page += 1

    # ----------------------------------------------------------------- reads

    def organization(self, org: str) -> dict:
        response = self._get(f"/orgs/{org}")
        if response.status_code != 200:
            raise GitHubError(
                f"could not read organization {org} ({response.status_code})"
            )
        return response.json()

    def seat_price_key(self, org: str) -> str:
        """Map the org's GitHub plan onto a seat price bucket.

        The plan is only visible when the installation has the right scope; an
        unreadable plan falls back to the cheaper Team rate so the estimate
        errs low. Overstating a saving is worse than understating one.
        """

        try:
            plan = str(self.organization(org).get("plan", {}).get("name", "")).lower()
        except GitHubError:
            return config.DEFAULT_SEAT_PRICE_KEY

        if "enterprise" in plan:
            return "enterprise"
        if "team" in plan:
            return "team"
        if "free" in plan:
            return "free"
        return config.DEFAULT_SEAT_PRICE_KEY

    def repositories(self, *, limit: int = config.MAX_REPOS_PER_SCAN) -> list[Repository]:
        """Every repo this installation can see."""

        repositories: list[Repository] = []
        page = 1
        while len(repositories) < limit:
            response = self._get("/installation/repositories", per_page=100, page=page)
            if response.status_code != 200:
                raise GitHubError(
                    f"could not list repositories ({response.status_code})"
                )
            batch = response.json().get("repositories", [])
            if not batch:
                break
            for item in batch:
                repositories.append(
                    Repository(
                        name=item["name"],
                        full_name=item["full_name"],
                        # Older payloads omit `visibility` but carry `private`.
                        visibility=item.get(
                            "visibility", "private" if item.get("private") else "public"
                        ),
                        archived=bool(item.get("archived", False)),
                    )
                )
            if len(batch) < 100:
                break
            page += 1
        return repositories[:limit]

    def outside_collaborators(self, repo_full_name: str) -> list[Collaborator]:
        """Outside collaborators on one repo.

        `affiliation=outside` is what makes this different from listing
        collaborators generally -- organization members are excluded, which is
        the whole population SCIM already handles.
        """

        found: list[Collaborator] = []
        for item in self._paginate(
            f"/repos/{repo_full_name}/collaborators", affiliation="outside"
        ):
            permissions = item.get("permissions", {})
            if permissions.get("admin"):
                permission = "admin"
            elif permissions.get("maintain"):
                permission = "maintain"
            elif permissions.get("push"):
                permission = "write"
            elif permissions.get("triage"):
                permission = "triage"
            else:
                permission = "read"

            found.append(
                Collaborator(
                    login=item["login"],
                    repo_full_name=repo_full_name,
                    permission=permission,
                )
            )
        return found

    def last_commit_at(self, repo_full_name: str, login: str) -> datetime | None:
        """When this user last committed to this repo, if ever.

        Commits are the cheapest activity signal GitHub exposes per-user
        per-repo. It is deliberately not treated as proof of inactivity --
        a reviewer who only comments on pull requests leaves no commits --
        so the caller presents this as "no commit activity", never as
        "inactive". See `analysis.py`.
        """

        response = self._get(
            f"/repos/{repo_full_name}/commits", author=login, per_page=1
        )
        if response.status_code in (404, 409):  # 409: empty repository
            return None
        if response.status_code != 200:
            raise GitHubError(
                f"could not read commits for {login} on {repo_full_name} "
                f"({response.status_code})"
            )

        commits = response.json()
        if not commits:
            return None

        committed = (
            commits[0].get("commit", {}).get("author", {}).get("date")
            or commits[0].get("commit", {}).get("committer", {}).get("date")
        )
        if not committed:
            return None
        return datetime.fromisoformat(committed.replace("Z", "+00:00"))
