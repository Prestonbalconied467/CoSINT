"""
tools/social/developer.py  –  Developer & research community platform handlers.

Platforms (no API key):
  stackoverflow.com / stackexchange.com → stackoverflow()  [Stack Exchange API v2.3]
  news.ycombinator.com                  → hackernews()     [Firebase JSON + Algolia]
  lobste.rs                             → lobsters()       [public JSON API]
  dev.to                                → devto()          [Dev.to public API]
  pypi.org/user/*                       → pypi()           [xmlrpc.client + httpx]
  npmjs.com                             → npm()            [npm registry search]
  orcid.org                             → orcid()          [ORCID public API]
  keybase.io                            → keybase()        [Keybase REST API]
  pastebin.com/u/*                      → pastebin()       [HTML scrape]
"""

import asyncio
import re
import xmlrpc.client
from urllib.parse import urlparse

import httpx

from shared import config
from shared.rate_limiter import rate_limit
from ._helpers import _BROWSER_UA, _ts, _clean_html


# ── Stack Overflow / Stack Exchange ───────────────────────────────────────


async def stackoverflow(user_id: str, site: str = "stackoverflow") -> str:
    """
    Stack Overflow / Stack Exchange profile via Stack Exchange API v2.3. No key
    required for public data (key raises daily quota from 300 to 10,000 requests).
    Extracts: display name, real name (if set), location, about, website link,
    account creation date, reputation, badge counts, top answer tags (= technology
    skill fingerprint), and associated accounts across the SE network.
    KEY PIVOT: top tags reveal exact technical stack; associated accounts may include
    other SE sites with different user profiles; website → domain chain.
    Optional: STACKEXCHANGE_API_KEY in config/env for higher rate limit.
    """
    key_param = {}
    if getattr(config, "STACKEXCHANGE_API_KEY", None):
        key_param["key"] = config.STACKEXCHANGE_API_KEY

    try:
        await rate_limit("default")
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"https://api.stackexchange.com/2.3/users/{user_id}",
                params={"site": site, "filter": "!9YdnSLm7x", **key_param},
            )
            r.raise_for_status()
            items = r.json().get("items", [])
            if not items:
                return f"Stack Overflow: user ID '{user_id}' not found on {site}."
            user = items[0]
            uid = user["user_id"]

            tags_r = await client.get(
                f"https://api.stackexchange.com/2.3/users/{uid}/top-answer-tags",
                params={"site": site, "pagesize": 10, **key_param},
            )
            top_tags = (
                tags_r.json().get("items", []) if tags_r.status_code == 200 else []
            )

            assoc_r = await client.get(
                f"https://api.stackexchange.com/2.3/users/{uid}/associated",
                params={"pagesize": 20, **key_param},
            )
            associated = (
                assoc_r.json().get("items", []) if assoc_r.status_code == 200 else []
            )
    except Exception as exc:
        return f"Stack Overflow error: {exc}"

    badges = user.get("badge_counts", {})
    about = _clean_html(user.get("about_me", "") or "")
    link_url = user.get("link", f"https://stackoverflow.com/users/{uid}")
    website = user.get("website_url", "N/A") or "N/A"

    lines = [
        f"Stack Overflow profile: {user.get('display_name', 'N/A')}\n",
        f"Display name:  {user.get('display_name', 'N/A')}",
        f"Location:      {user.get('location', 'N/A') or 'N/A'}",
        f"Website:       {website}",
        f"About:         {about[:200] or 'N/A'}",
        f"Reputation:    {user.get('reputation', 0):,}",
        f"Badges:        🥇{badges.get('gold', 0)}  🥈{badges.get('silver', 0)}  🥉{badges.get('bronze', 0)}",
        f"Created:       {_ts(user.get('creation_date', 0))}",
        f"Last seen:     {_ts(user.get('last_access_date', 0))}",
        f"User ID:       {uid}",
        f"Profile URL:   {link_url}",
    ]

    if top_tags:
        lines.append("\n── Top Tags (Technology Fingerprint) ──")
        for tag in top_tags[:10]:
            lines.append(
                f"  {tag.get('tag_name', '?'):25} "
                f"answer score: {tag.get('answer_score', 0):,}  "
                f"({tag.get('answer_count', 0)} answers)"
            )

    if associated:
        lines.append(f"\n── Associated SE Accounts ({len(associated)}) ──")
        for acc in associated[:10]:
            lines.append(
                f"  {acc.get('site_name', '?'):25} rep: {acc.get('reputation', 0):,}  "
                f"{acc.get('site_url', '')}/users/{acc.get('user_id', '')}"
            )

    return "\n".join(lines)


# ── HackerNews ────────────────────────────────────────────────────────────


async def hackernews(username: str) -> str:
    """
    HackerNews via Firebase JSON API + Algolia search. No key required.
    Extracts: karma, creation date, about text (often real name/email/employer),
    recent submissions + scores, domain frequency table.
    KEY PIVOT: about field is free-form and frequently contains real identity data.
    """
    try:
        await rate_limit("default")
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"https://hacker-news.firebaseio.com/v0/user/{username}.json"
            )
            r.raise_for_status()
            user = r.json()
            if user is None:
                return f"HackerNews: user '{username}' not found."

            alg_r = await client.get(
                "https://hn.algolia.com/api/v1/search",
                params={"tags": f"author_{username}", "hitsPerPage": 20},
            )
            hits = alg_r.json().get("hits", []) if alg_r.status_code == 200 else []
    except Exception as exc:
        return f"HackerNews error: {exc}"

    about = _clean_html(user.get("about", "") or "")
    lines = [
        f"HackerNews profile: {username}\n",
        f"Karma:         {user.get('karma', 0):,}",
        f"Created:       {_ts(user.get('created', 0))}",
        f"About:         {about or 'N/A'}",
        f"Submissions:   {len(user.get('submitted', [])):,}",
        f"Profile URL:   https://news.ycombinator.com/user?id={username}",
    ]

    if hits:
        lines.append(f"\n── Recent Submissions ({len(hits)}) ──")
        for hit in hits[:10]:
            title = hit.get("title") or hit.get("story_title") or "(comment)"
            pts = hit.get("points", 0) or 0
            lines.append(f"  {title[:72]:73} ↑{pts}")

    domains: dict[str, int] = {}
    for hit in hits:
        d = urlparse(hit.get("url", "")).netloc.lower().replace("www.", "")
        if d:
            domains[d] = domains.get(d, 0) + 1
    if domains:
        lines.append("\n── Shared domains ──")
        for d, cnt in sorted(domains.items(), key=lambda x: -x[1])[:10]:
            lines.append(f"  {d:40} ({cnt}×)")

    return "\n".join(lines)


# ── Lobste.rs ─────────────────────────────────────────────────────────────


async def lobsters(username: str) -> str:
    """
    Lobste.rs profile via public JSON API. No key required.
    Extracts: bio, invited-by chain, GitHub + Twitter links, creation date, karma,
    and recent stories with tags.
    KEY PIVOT: the invitation tree directly maps social trust relationships and can
    reveal the real-world network behind a pseudonymous community identity.
    Every member was personally vouched for by another — invitation chain = social graph.
    """
    try:
        await rate_limit("default")
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"https://lobste.rs/u/{username}.json")
            if r.status_code == 404:
                return f"Lobste.rs: user '{username}' not found."
            r.raise_for_status()
            user = r.json()
    except Exception as exc:
        return f"Lobste.rs error: {exc}"

    lines = [
        f"Lobste.rs profile: {username}\n",
        f"Username:      {user.get('username', 'N/A')}",
        f"Bio:           {(user.get('about', '') or 'N/A')[:200]}",
        f"GitHub:        {user.get('github_username', 'N/A') or 'N/A'}",
        f"Twitter:       {user.get('twitter_username', 'N/A') or 'N/A'}",
        f"Invited by:    {user.get('invited_by_user', 'N/A') or 'N/A'}",
        f"Karma:         {user.get('karma', 0):,}",
        f"Joined:        {(user.get('created_at', 'N/A') or 'N/A')[:10]}",
        f"Profile URL:   https://lobste.rs/u/{username}",
    ]

    stories = user.get("stories", [])
    if stories:
        all_tags: list[str] = []
        lines.append(f"\n── Recent Stories ({len(stories)}) ──")
        for s in stories[:10]:
            tags = s.get("tags", [])
            all_tags.extend(tags)
            lines.append(
                f"  {s.get('title', '?')[:60]:62} ↑{s.get('score', 0)}  [{', '.join(tags[:3])}]"
            )
        if all_tags:
            from collections import Counter

            top = Counter(all_tags).most_common(6)
            lines.append("\n── Top Tags ──")
            lines.append("  " + "  ".join(f"{t}({c})" for t, c in top))

    invitees = user.get("invited_users", [])
    if invitees:
        lines.append(f"\n── Users Invited by {username} ({len(invitees)}) ──")
        for inv in invitees[:15]:
            lines.append(f"  {inv.get('username', '?')}")

    return "\n".join(lines)


# ── Dev.to ────────────────────────────────────────────────────────────────


async def devto(username: str) -> str:
    """
    Dev.to profile via public API. No key required.
    Extracts: name, bio, location, website, GitHub handle, Twitter handle,
    joined date, post count, comment count, and article list with tags.
    KEY PIVOT: GitHub + Twitter fields directly correlate accounts; tags across
    articles form a strong technology/interest fingerprint.
    """
    try:
        await rate_limit("default")
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://dev.to/api/users/by_username", params={"url": username}
            )
            r.raise_for_status()
            user = r.json()

            arts_r = await client.get(
                "https://dev.to/api/articles",
                params={"username": username, "per_page": 10},
            )
            articles = arts_r.json() if arts_r.status_code == 200 else []
    except Exception as exc:
        return f"Dev.to error: {exc}"

    lines = [
        f"Dev.to profile: {username}\n",
        f"Name:          {user.get('name', 'N/A')}",
        f"Bio:           {(user.get('summary', '') or 'N/A')[:200]}",
        f"Location:      {user.get('location', 'N/A') or 'N/A'}",
        f"Website:       {user.get('website_url', 'N/A') or 'N/A'}",
        f"GitHub:        {user.get('github_username', 'N/A') or 'N/A'}",
        f"Twitter:       {user.get('twitter_username', 'N/A') or 'N/A'}",
        f"Joined:        {(user.get('joined_at', 'N/A') or 'N/A')[:10]}",
        f"Profile URL:   https://dev.to/{username}",
    ]

    if isinstance(articles, list) and articles:
        all_tags: list[str] = []
        lines.append(f"\n── Recent Articles ({len(articles)}) ──")
        for a in articles:
            tags = a.get("tag_list", [])
            all_tags.extend(tags)
            lines.append(
                f"  {a.get('title', '?')[:60]:62} "
                f"❤ {a.get('positive_reactions_count', 0):,}  "
                f"{', '.join(tags[:3])}"
            )
        if all_tags:
            from collections import Counter

            top = Counter(all_tags).most_common(8)
            lines.append("\n── Top Tags ──")
            lines.append("  " + "  ".join(f"{tag}({cnt})" for tag, cnt in top))

    return "\n".join(lines)


# ── PyPI ──────────────────────────────────────────────────────────────────


async def pypi(username: str) -> str:
    """
    PyPI via XMLRPC user_packages() + per-package JSON API. No key required.
    Extracts: all packages (owner/maintainer roles), versions, author emails.
    KEY PIVOT: author_email often contains real personal emails from pre-OPSEC era.
    """

    def _fetch() -> list[tuple[str, str]]:
        try:
            return xmlrpc.client.ServerProxy("https://pypi.org/pypi").user_packages(
                username
            )  # type: ignore
        except Exception:
            return []

    packages: list[tuple[str, str]] = await asyncio.to_thread(_fetch)
    if not packages:
        return f"PyPI: no packages found for '{username}'."

    lines = [
        f"PyPI profile: {username}\n",
        f"Packages:      {len(packages)}",
        f"Profile URL:   https://pypi.org/user/{username}/",
        "\n── Packages ──",
    ]

    emails: set[str] = set()
    async with httpx.AsyncClient(timeout=15) as client:
        for name, role in packages[:20]:
            try:
                await rate_limit("default")
                r = await client.get(f"https://pypi.org/pypi/{name}/json")
                if r.status_code == 200:
                    info = r.json().get("info", {})
                    version = info.get("version", "?")
                    for e in re.findall(
                        r"[\w.+-]+@[\w-]+\.[\w.]+", info.get("author_email", "") or ""
                    ):
                        emails.add(e)
                    lines.append(
                        f"  {name:35} [{role:10}]  v{version:<12}"
                        f"https://pypi.org/project/{name}/"
                    )
                else:
                    lines.append(f"  {name:35} [{role}]")
            except Exception:
                lines.append(f"  {name:35} [{role}]")

    if emails:
        lines.append("\n── Emails from package metadata ──")
        for e in sorted(emails):
            lines.append(f"  {e}")

    return "\n".join(lines)


# ── npm ───────────────────────────────────────────────────────────────────


async def npm(username: str) -> str:
    """
    npm via registry search API (maintainer:<username>). No key required.
    Extracts: all maintained packages, versions, descriptions, publisher emails.
    KEY PIVOT: publisher.email is frequently a real personal email.
    """
    try:
        await rate_limit("default")
        async with httpx.AsyncClient(
            timeout=15, headers={"User-Agent": _BROWSER_UA}
        ) as client:
            r = await client.get(
                "https://registry.npmjs.org/-/v1/search",
                params={"text": f"maintainer:{username}", "size": 25},
            )
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        return f"npm error: {exc}"

    objects = data.get("objects", [])
    if not objects:
        return f"npm: no packages found for maintainer '{username}'."

    lines = [
        f"npm profile: {username}\n",
        f"Packages:      {data.get('total', len(objects))}",
        f"Profile URL:   https://www.npmjs.com/~{username}",
        "\n── Packages ──",
    ]

    emails: set[str] = set()
    for obj in objects:
        pkg = obj.get("package", {})
        email = pkg.get("publisher", {}).get("email", "")
        if email and "@" in email:
            emails.add(email)
        lines.append(
            f"  {pkg.get('name', '?'):35} v{pkg.get('version', '?'):<12} "
            f"{(pkg.get('description', '') or '')[:55]}"
        )

    if emails:
        lines.append("\n── Publisher emails ──")
        for e in sorted(emails):
            lines.append(f"  {e}")

    return "\n".join(lines)


# ── ORCID ─────────────────────────────────────────────────────────────────


async def orcid(orcid_id: str) -> str:
    """
    ORCID researcher profile via public API v3. No key required.
    Extracts: real name, credit name, biography, keywords, external identifiers
    (ResearcherID, Scopus, Google Scholar), employment history, education history,
    affiliated organisations, and top-cited publications.
    KEY PIVOT: one of the only sources that reliably gives real name + employer +
    education for academic/researcher subjects; external IDs cross-link to Scopus,
    Web of Science, Google Scholar.
    """
    orcid_id = orcid_id.strip().upper()
    try:
        await rate_limit("default")
        async with httpx.AsyncClient(
            timeout=15,
            headers={"Accept": "application/json"},
        ) as client:
            r = await client.get(f"https://pub.orcid.org/v3.0/{orcid_id}/record")
            if r.status_code == 404:
                return f"ORCID: ID '{orcid_id}' not found."
            r.raise_for_status()
            record = r.json()
    except Exception as exc:
        return f"ORCID error: {exc}"

    person = record.get("person", {})
    name_data = person.get("name", {}) or {}
    bio = (person.get("biography", {}) or {}).get("content", "") or ""
    keywords = [
        (k.get("content", ""))
        for k in (person.get("keywords", {}) or {}).get("keyword", [])
    ]
    ext_ids = (person.get("external-identifiers", {}) or {}).get(
        "external-identifier", []
    )

    given = (name_data.get("given-names", {}) or {}).get("value", "") or ""
    family = (name_data.get("family-name", {}) or {}).get("value", "") or ""
    credit = (name_data.get("credit-name", {}) or {}).get("value", "") or ""

    activities = record.get("activities-summary", {}) or {}
    employments = (activities.get("employments", {}) or {}).get("affiliation-group", [])
    educations = (activities.get("educations", {}) or {}).get("affiliation-group", [])
    works = (activities.get("works", {}) or {}).get("group", [])

    def _affil(group_list: list, limit: int = 5) -> list[str]:
        result = []
        for group in group_list[:limit]:
            for summary in group.get("summaries", []):
                for key in ("employment-summary", "education-summary"):
                    s = summary.get(key, {})
                    if not s:
                        continue
                    org = (s.get("organization", {}) or {}).get("name", "?")
                    role = s.get("role-title", "") or ""
                    dept = s.get("department-name", "") or ""
                    start = ((s.get("start-date") or {}).get("year", {}) or {}).get(
                        "value", ""
                    ) or ""
                    end = ((s.get("end-date") or {}).get("year", {}) or {}).get(
                        "value", "present"
                    )
                    period = f"{start}–{end}" if start else ""
                    result.append(f"  {org:35} {role or dept:30} {period}")
        return result

    lines = [
        f"ORCID profile: {orcid_id}\n",
        f"Full name:     {given} {family}".strip(),
        f"Credit name:   {credit or 'N/A'}",
        f"Bio:           {bio[:200] or 'N/A'}",
        f"Keywords:      {', '.join(keywords[:10]) or 'N/A'}",
        f"Profile URL:   https://orcid.org/{orcid_id}",
    ]

    if ext_ids:
        lines.append("\n── External Identifiers ──")
        for eid in ext_ids:
            id_type = eid.get("external-id-type", "?")
            id_val = eid.get("external-id-value", "?")
            id_url = (eid.get("external-id-url", {}) or {}).get("value", "")
            lines.append(f"  {id_type:20} {id_val:20} {id_url}")

    emp_lines = _affil(employments)
    if emp_lines:
        lines.append("\n── Employment ──")
        lines.extend(emp_lines)

    edu_lines = _affil(educations)
    if edu_lines:
        lines.append("\n── Education ──")
        lines.extend(edu_lines)

    if works:
        lines.append(f"\n── Works ({len(works)} total, showing 10) ──")
        for work_group in works[:10]:
            ws = work_group.get("work-summary", [{}])[0]
            title = ((ws.get("title", {}) or {}).get("title", {}) or {}).get(
                "value", "?"
            )
            year = ((ws.get("publication-date") or {}).get("year", {}) or {}).get(
                "value", ""
            ) or ""
            lines.append(f"  {year}  {title[:80]}")

    return "\n".join(lines)


# ── Keybase ───────────────────────────────────────────────────────────────


async def keybase(username: str) -> str:
    """
    Keybase via public REST API. No key required.
    Extracts: full name, bio, location, cryptographically verified identity proofs
    (Twitter, GitHub, Reddit, HN, website, DNS), PGP fingerprints.
    KEY PIVOT: identity proofs directly correlate usernames across platforms with
    cryptographic certainty — the single richest cross-platform pivot available.
    """
    try:
        await rate_limit("default")
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://keybase.io/_/api/1.0/user/lookup.json",
                params={"username": username},
            )
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        return f"Keybase error: {exc}"

    if data.get("status", {}).get("code") != 0:
        return f"Keybase: user '{username}' not found."

    them = data.get("them", [])
    if not them:
        return f"Keybase: user '{username}' not found."
    user = them[0]

    basics = user.get("basics", {})
    profile = user.get("profile", {})
    proofs = user.get("proofs_summary", {}).get("all", [])
    pgp = user.get("public_keys", {}).get("primary", {})

    lines = [
        f"Keybase profile: {username}\n",
        f"Full name:     {profile.get('full_name', 'N/A') or 'N/A'}",
        f"Bio:           {(profile.get('bio', '') or 'N/A')[:200]}",
        f"Location:      {profile.get('location', 'N/A') or 'N/A'}",
        f"Twitter:       {profile.get('twitter', 'N/A') or 'N/A'}",
        f"Website:       {profile.get('website', 'N/A') or 'N/A'}",
        f"Created:       {_ts(basics.get('ctime', 0))}",
        f"Profile URL:   https://keybase.io/{username}",
    ]

    if pgp:
        lines.append("\n── PGP Primary Key ──")
        lines.append(f"  Fingerprint:   {pgp.get('key_fingerprint', 'N/A')}")
        lines.append(
            f"  Key:           {pgp.get('bits', '?')}-bit  algo {pgp.get('algo', '?')}"
        )

    if proofs:
        lines.append(f"\n── Identity Proofs ({len(proofs)}) ──")
        for proof in proofs:
            verified = "✓" if proof.get("state") == 1 else "✗"
            lines.append(
                f"  {verified} {proof.get('proof_type', '?'):15} "
                f"{proof.get('nametag', '?'):25} {proof.get('service_url', '')}"
            )

    return "\n".join(lines)


# ── Pastebin ──────────────────────────────────────────────────────────────


async def pastebin(username: str) -> str:
    """
    Pastebin public profile via HTML scrape. No key required.
    Extracts: member since date, linked website, public paste titles + URLs.
    KEY PIVOT: paste contents may contain credentials, API keys, config files, PII —
    fetch each individually and keyword-search for high-value data.
    """
    url = f"https://pastebin.com/u/{username}"
    try:
        await rate_limit("default")
        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": _BROWSER_UA},
        ) as client:
            r = await client.get(url)
            if r.status_code == 404:
                return f"Pastebin: user '{username}' not found."
            r.raise_for_status()
    except Exception as exc:
        return f"Pastebin error: {exc}"

    html = r.text

    ms = re.search(r"Member Since[^>]*>([^<]+)<", html)
    member_since = ms.group(1).strip() if ms else "N/A"

    ws = re.search(r'class="[^"]*globe[^"]*"[^>]*>[^<]*<a href="([^"]+)"', html)
    website = ws.group(1) if ws else "N/A"

    seen: set[str] = set()
    pastes: list[tuple[str, str]] = []
    for pid, title in re.findall(
        r'<a href="/([A-Za-z0-9]{8})"[^>]*>\s*([^<]{1,80}?)\s*</a>', html
    ):
        if pid not in seen and title.strip():
            seen.add(pid)
            pastes.append((pid, title.strip()))

    lines = [
        f"Pastebin profile: {username}\n",
        f"Member since:  {member_since}",
        f"Website:       {website}",
        f"Profile URL:   {url}",
    ]
    if pastes:
        lines.append(f"\n── Public Pastes ({len(pastes)}) ──")
        for pid, title in pastes[:20]:
            lines.append(f"  https://pastebin.com/{pid}  {title[:60]}")
    else:
        lines.append("\nNo public pastes found.")

    return "\n".join(lines)
