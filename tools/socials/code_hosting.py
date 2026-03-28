"""
tools/social/code_hosting.py  –  Git hosting / code platform handlers.

Platforms:
  github.com    → github()     [GitHub REST API v3; optional GITHUB_TOKEN]
  gitlab.com    → gitlab()     [GitLab REST API v4; no key required]
  bitbucket.org → bitbucket()  [Bitbucket REST API v2; no key required]
"""

import re

import httpx

from shared import config
from shared.rate_limiter import rate_limit
from ._helpers import _BROWSER_UA


# ── GitHub ────────────────────────────────────────────────────────────────


async def github(username: str) -> str:
    """
    GitHub profile via GitHub REST API v3.
    Extracts: name, bio, email, location, website, company, Twitter handle,
    creation date, public repo count, gists, followers/following, recent repos
    with language and star counts, and unique committer emails from public events.
    KEY PIVOT: commit emails are the highest-value artifact — developers often
    committed with personal emails before any OPSEC awareness. Extract every unique
    address and run the full email chain on each.
    Also: org memberships → company chain; website → domain chain;
    contribution timestamps → working hours → likely timezone.
    No key required (60 req/hr). Optional: GITHUB_TOKEN raises limit to 5000 req/hr.
    """
    hdrs: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if getattr(config, "GITHUB_TOKEN", None):
        hdrs["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"

    try:
        await rate_limit("github")
        async with httpx.AsyncClient(timeout=15, headers=hdrs) as client:
            r = await client.get(f"https://api.github.com/users/{username}")
            if r.status_code == 404:
                return f"GitHub: user '{username}' not found."
            r.raise_for_status()
            user = r.json()
    except Exception as exc:
        return f"GitHub error: {exc}"

    lines = [
        f"GitHub profile: {username}\n",
        f"Name:          {user.get('name', 'N/A')}",
        f"Bio:           {user.get('bio', 'N/A')}",
        f"Email:         {user.get('email', 'N/A')}",
        f"Location:      {user.get('location', 'N/A')}",
        f"Website:       {user.get('blog', 'N/A')}",
        f"Company:       {user.get('company', 'N/A')}",
        f"Twitter:       {user.get('twitter_username', 'N/A')}",
        f"Created:       {user.get('created_at', 'N/A')}",
        f"Last push:     {user.get('updated_at', 'N/A')}",
        f"Public repos:  {user.get('public_repos', 0)}",
        f"Gists:         {user.get('public_gists', 0)}",
        f"Followers:     {user.get('followers', 0):,}",
        f"Following:     {user.get('following', 0):,}",
        f"Profile URL:   {user.get('html_url', 'N/A')}",
    ]

    # Recent repos
    try:
        await rate_limit("github")
        async with httpx.AsyncClient(timeout=15, headers=hdrs) as client:
            rr = await client.get(
                f"https://api.github.com/users/{username}/repos",
                params={"sort": "updated", "per_page": 20},
            )
            repos = rr.json() if rr.status_code == 200 else []
        if repos:
            lines.append("\n── Recent Repos ──")
            for repo in repos:
                lang = repo.get("language") or "N/A"
                stars = repo.get("stargazers_count", 0)
                lines.append(
                    f"  {repo.get('name', '?')} [{lang}]  ★{stars} {repo.get('html_url', '')}"
                )
    except Exception:
        pass

    # Commit emails from public events
    emails: set[str] = set()
    try:
        await rate_limit("github")
        async with httpx.AsyncClient(timeout=15, headers=hdrs) as client:
            er = await client.get(
                f"https://api.github.com/users/{username}/events/public",
                params={"per_page": 30},
            )
            events = er.json() if er.status_code == 200 else []
        for event in events:
            payload = str(event.get("payload", ""))
            for e in re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", payload):
                if not e.endswith("@users.noreply.github.com"):
                    emails.add(e)
    except Exception:
        pass

    if emails:
        lines.append("\n── Emails from commits ──")
        for e in sorted(emails):
            lines.append(f"  {e}")

    return "\n".join(lines)


# ── GitLab ────────────────────────────────────────────────────────────────


async def gitlab(username: str) -> str:
    """
    GitLab profile via public REST API v4. No key required.
    Extracts: bio, location, website, org, LinkedIn, Twitter, public repos with star
    counts, and unique emails scraped from push event payloads.
    KEY PIVOT: push-event emails are the same high-value pivot as GitHub commit emails.
    """
    base = "https://gitlab.com/api/v4"
    hdrs = {"User-Agent": _BROWSER_UA}
    try:
        await rate_limit("default")
        async with httpx.AsyncClient(timeout=15, headers=hdrs) as client:
            r = await client.get(f"{base}/users", params={"username": username})
            r.raise_for_status()
            users = r.json()
            if not users:
                return f"GitLab: user '{username}' not found."
            user = users[0]
            uid = user["id"]

            proj_r = await client.get(
                f"{base}/users/{uid}/projects",
                params={
                    "order_by": "last_activity_at",
                    "per_page": 20,
                    "visibility": "public",
                },
            )
            projects = proj_r.json() if proj_r.status_code == 200 else []

            ev_r = await client.get(
                f"{base}/users/{uid}/events",
                params={"per_page": 100, "action": "pushed"},
            )
            events = ev_r.json() if ev_r.status_code == 200 else []
    except Exception as exc:
        return f"GitLab error: {exc}"

    lines = [
        f"GitLab profile: {username}\n",
        f"Name:          {user.get('name', 'N/A')}",
        f"Username:      @{user.get('username', 'N/A')}",
        f"Bio:           {user.get('bio') or 'N/A'}",
        f"Location:      {user.get('location') or 'N/A'}",
        f"Website:       {user.get('website_url') or 'N/A'}",
        f"Organization:  {user.get('organization') or 'N/A'}",
        f"LinkedIn:      {user.get('linkedin') or 'N/A'}",
        f"Twitter:       {user.get('twitter') or 'N/A'}",
        f"Created:       {user.get('created_at', 'N/A')}",
        f"Followers:     {user.get('followers', 0):,}",
        f"Following:     {user.get('following', 0):,}",
        f"Profile URL:   {user.get('web_url', 'N/A')}",
    ]

    if projects:
        lines.append("\n── Public Repos ──")
        for p in projects:
            lines.append(
                f"  {p.get('name', '?')} ★{p.get('star_count', 0)} {p.get('web_url', '')}"
            )

    emails: set[str] = set()
    for ev in events:
        for e in re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", str(ev)):
            if "noreply" not in e:
                emails.add(e)
    if emails:
        lines.append("\n── Emails from push events ──")
        for e in sorted(emails):
            lines.append(f"  {e}")

    return "\n".join(lines)


# ── Bitbucket ─────────────────────────────────────────────────────────────


async def bitbucket(username: str) -> str:
    """
    Bitbucket profile via Atlassian REST API v2. No key required for public data.
    Extracts: display name, location, website, account creation date, public repos
    with language and size, and emails leaked from commit history via the diff API.
    KEY PIVOT: commit emails in public repos — same pivot as GitHub/GitLab.
    """
    base = "https://api.bitbucket.org/2.0"
    hdrs = {"User-Agent": _BROWSER_UA}
    try:
        await rate_limit("default")
        async with httpx.AsyncClient(timeout=15, headers=hdrs) as client:
            r = await client.get(f"{base}/users/{username}")
            if r.status_code == 404:
                # Try workspaces (org accounts)
                r = await client.get(f"{base}/workspaces/{username}")
            r.raise_for_status()
            user = r.json()

            repos_r = await client.get(
                f"{base}/repositories/{username}",
                params={"pagelen": 20, "sort": "-updated_on"},
            )
            repos = (
                repos_r.json().get("values", []) if repos_r.status_code == 200 else []
            )

            # Attempt to scrape emails from commits on the first public repo
            emails: set[str] = set()
            if repos:
                slug = repos[0].get("slug", "")
                commits_r = await client.get(
                    f"{base}/repositories/{username}/{slug}/commits",
                    params={"pagelen": 30},
                )
                if commits_r.status_code == 200:
                    for commit in commits_r.json().get("values", []):
                        author = commit.get("author", {})
                        raw = author.get("raw", "")
                        for e in re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", raw):
                            if "noreply" not in e:
                                emails.add(e)
    except Exception as exc:
        return f"Bitbucket error: {exc}"

    lines = [
        f"Bitbucket profile: {username}\n",
        f"Display name:  {user.get('display_name', 'N/A')}",
        f"Account type:  {user.get('account_type', 'N/A')}",
        f"Location:      {user.get('location', 'N/A') or 'N/A'}",
        f"Website:       {user.get('website', 'N/A') or 'N/A'}",
        f"Created:       {(user.get('created_on', 'N/A') or 'N/A')[:10]}",
        f"Account ID:    {user.get('account_id', 'N/A')}",
        f"Profile URL:   https://bitbucket.org/{username}",
    ]

    if repos:
        lines.append("\n── Public Repos ──")
        for repo in repos:
            lang = repo.get("language", "N/A") or "N/A"
            size = repo.get("size", 0)
            lines.append(
                f"  {repo.get('name', '?')} [{lang}] {size // 1024}KB  "
                f"https://bitbucket.org/{username}/{repo.get('slug', '')}"
            )

    if emails:
        lines.append("\n── Emails from commits ──")
        for e in sorted(emails):
            lines.append(f"  {e}")

    return "\n".join(lines)
