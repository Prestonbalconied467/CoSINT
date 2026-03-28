"""
tools/social/misc.py  –  Miscellaneous platform handlers.

Platforms (no API key):
  linktr.ee      → linktree()   [HTML scrape + __NEXT_DATA__ JSON]
  hub.docker.com → dockerhub()  [Docker Hub API v2]
  duolingo.com   → duolingo()   [public API]

Platforms (API key required):
  gravatar.com   → gravatar()   [GRAVATAR_API_KEY]
"""

import hashlib
import json as _json
import re

import httpx

from shared import config
from shared.rate_limiter import rate_limit
from ._helpers import _BROWSER_UA, _ts


# ── Linktree ──────────────────────────────────────────────────────────────


async def linktree(username: str) -> str:
    """
    Linktree profile via HTML scrape + embedded __NEXT_DATA__ JSON. No key required.
    Extracts: display name, bio, avatar URL, all published links (label + URL).
    KEY PIVOT: Linktree is the single page where subjects deliberately aggregate
    all their social accounts — it's the richest cross-platform link source available
    after Keybase. Every link is a new chain entry point.
    """
    url = f"https://linktr.ee/{username}"
    try:
        await rate_limit("default")
        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": _BROWSER_UA},
        ) as client:
            r = await client.get(url)
            if r.status_code == 404:
                return f"Linktree: profile '{username}' not found."
            r.raise_for_status()
    except Exception as exc:
        return f"Linktree error: {exc}"

    match = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        r.text,
        re.DOTALL,
    )
    if not match:
        return f"Linktree: could not parse data for '{username}'."

    try:
        data = _json.loads(match.group(1))
        account = data.get("props", {}).get("pageProps", {}).get("account", {})
        links = data.get("props", {}).get("pageProps", {}).get("links", [])
    except Exception:
        return f"Linktree: failed to parse JSON for '{username}'."

    if not account and not links:
        return f"Linktree: no data found for '{username}'."

    lines = [
        f"Linktree profile: {username}\n",
        f"Display name:  {account.get('name', 'N/A') or 'N/A'}",
        f"Bio:           {(account.get('description', '') or 'N/A')}",
        f"Profile URL:   {url}",
    ]

    if links:
        lines.append(f"\n── Links ({len(links)}) ──")
        for link in links:
            title = link.get("title", "") or link.get("type", "?")
            href = link.get("url", "") or ""
            if href:
                lines.append(f"  {title:35} {href}")

    return "\n".join(lines)


# ── Docker Hub ────────────────────────────────────────────────────────────


async def dockerhub(username: str) -> str:
    """
    Docker Hub profile via Docker Hub API v2. No key required for public data.
    Extracts: full name, company, location, biography, profile URL, public repo count,
    and list of public repositories with descriptions, pull counts and star counts.
    KEY PIVOT: company + location → org/geo; popular images reveal tech stack used.
    """
    try:
        await rate_limit("default")
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"https://hub.docker.com/v2/users/{username}/")
            if r.status_code == 404:
                return f"Docker Hub: user '{username}' not found."
            r.raise_for_status()
            user = r.json()

            repos_r = await client.get(
                f"https://hub.docker.com/v2/repositories/{username}/",
                params={"page_size": 50, "ordering": "last_updated"},
            )
            repos = (
                repos_r.json().get("results", []) if repos_r.status_code == 200 else []
            )
    except Exception as exc:
        return f"Docker Hub error: {exc}"

    lines = [
        f"Docker Hub profile: {username}\n",
        f"Full name:     {user.get('full_name', 'N/A') or 'N/A'}",
        f"Company:       {user.get('company', 'N/A') or 'N/A'}",
        f"Location:      {user.get('location', 'N/A') or 'N/A'}",
        f"Bio:           {(user.get('profile_url', '') or 'N/A')}",
        f"Joined:        {(user.get('date_joined', 'N/A') or 'N/A')[:10]}",
        f"Profile URL:   https://hub.docker.com/u/{username}",
    ]

    if repos:
        lines.append(f"\n── Public Repositories ({len(repos)}) ──")
        for repo in repos:
            pulls = repo.get("pull_count", 0)
            stars = repo.get("star_count", 0)
            desc = (repo.get("description", "") or "")[:50]
            lines.append(f"  {repo.get('name', '?')} ↓{pulls:,} ★{stars} {desc}")

    return "\n".join(lines)


# ── Gravatar ──────────────────────────────────────────────────────────────


async def gravatar(hash_or_email: str) -> str:
    """
    Gravatar profile via API v3. API key required (free tier available).
    Accepts either an MD5/SHA256 email hash or the path component from a gravatar.com URL.
    Extracts: display name, bio, location, verified accounts (social proofs across
    platforms), website, job title, company, pronunciation, pronouns, avatar URL.
    KEY PIVOT: verified accounts list cross-links this email hash to GitHub, Twitter,
    Mastodon, etc. — a Gravatar lookup on any discovered email immediately pivots
    to the subject's full social graph.
    Requires: GRAVATAR_API_KEY
    """
    api_key = getattr(config, "GRAVATAR_API_KEY", None)
    if not api_key:
        return "Gravatar: GRAVATAR_API_KEY not configured."

    if "@" in hash_or_email:
        profile_hash = hashlib.sha256(
            hash_or_email.strip().lower().encode()
        ).hexdigest()
    else:
        profile_hash = hash_or_email.strip().lower()

    try:
        await rate_limit("default")
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"https://api.gravatar.com/v3/profiles/{profile_hash}",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if r.status_code == 404:
                return f"Gravatar: no profile found for hash '{profile_hash}'."
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        return f"Gravatar error: {exc}"

    lines = [
        f"Gravatar profile: {profile_hash}\n",
        f"Display name:  {data.get('display_name', 'N/A') or 'N/A'}",
        f"Bio:           {(data.get('description', '') or 'N/A')}",
        f"Location:      {data.get('location', 'N/A') or 'N/A'}",
        f"Job title:     {data.get('job_title', 'N/A') or 'N/A'}",
        f"Company:       {data.get('company', 'N/A') or 'N/A'}",
        f"Pronunciation: {data.get('pronunciation', 'N/A') or 'N/A'}",
        f"Pronouns:      {data.get('pronouns', 'N/A') or 'N/A'}",
        f"Profile URL:   https://gravatar.com/{profile_hash}",
        f"Avatar URL:    https://www.gravatar.com/avatar/{profile_hash}",
    ]

    verified = data.get("verified_accounts", [])
    if verified:
        lines.append(f"\n── Verified Accounts ({len(verified)}) ──")
        for acc in verified:
            service = acc.get("service_label", "?")
            url = acc.get("url", "")
            lines.append(f"  {service:20} {url}")

    links = data.get("links", [])
    if links:
        lines.append(f"\n── Links ({len(links)}) ──")
        for lnk in links:
            lines.append(f"  {lnk.get('label', '?'):25} {lnk.get('url', '')}")

    interests = [i.get("name", "") for i in data.get("interests", []) if i.get("name")]
    if interests:
        lines.append("\n── Interests ──")
        lines.append("  " + ", ".join(interests[:20]))

    return "\n".join(lines)


# ── Duolingo ──────────────────────────────────────────────────────────────


async def duolingo(username: str) -> str:
    """
    Duolingo profile via public API. No key required.
    Extracts: display name, learning languages + levels (strong geo/culture signal),
    streak, total XP, creation date, profile picture, follower/following counts.
    KEY PIVOT: language combination → likely native language → nationality inference;
    streak data → daily activity time → timezone inference.
    """
    try:
        await rate_limit("default")
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://www.duolingo.com/2017-06-30/users",
                params={"username": username},
                headers={"User-Agent": _BROWSER_UA},
            )
            r.raise_for_status()
            data = r.json()
            users = data.get("users", [])
            if not users:
                return f"Duolingo: user '{username}' not found."
            user = users[0]
    except Exception as exc:
        return f"Duolingo error: {exc}"

    courses = user.get("courses", [])

    lines = [
        f"Duolingo profile: {username}\n",
        f"Display name:  {user.get('name', 'N/A') or 'N/A'}",
        f"Username:      {user.get('username', 'N/A')}",
        f"Streak:        {user.get('streak', 0)} days",
        f"Total XP:      {user.get('totalXp', 0):,}",
        f"Followers:     {user.get('numFollowers', 0):,}",
        f"Following:     {user.get('numFollowing', 0):,}",
        f"Joined:        {_ts(user.get('creationDate', 0))}",
        f"Profile URL:   https://www.duolingo.com/profile/{username}",
    ]

    if courses:
        lines.append(f"\n── Learning Languages ({len(courses)}) ──")
        for c in courses:
            ui_lang = c.get("fromLanguage", "?")
            learning = c.get("title", "?")
            xp = c.get("xp", 0)
            crowns = c.get("crowns", 0)
            lines.append(
                f"  {learning:20} (from {ui_lang:8})  XP: {xp:,}  Crowns: {crowns}"
            )

    return "\n".join(lines)
