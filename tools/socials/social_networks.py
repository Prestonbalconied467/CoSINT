"""
tools/social/social_networks.py  –  Social network platform handlers.

Platforms (no API key):
  instagram.com  → instagram()  [instaloader]
  tiktok.com     → tiktok()     [httpx; parses embedded JSON]
  bsky.app       → bluesky()    [AT Protocol public API]
  reddit.com     → reddit()     [public JSON API]

Platforms (API key required):
  twitter.com / x.com → twitter()  [TWITTER_BEARER_TOKEN]
  vk.com              → vk()       [VK_ACCESS_TOKEN optional]
  tumblr.com          → tumblr()   [TUMBLR_API_KEY]
"""

import asyncio
import datetime
import re

import httpx

from shared import config
from shared.rate_limiter import rate_limit
from ._helpers import _BROWSER_UA, _ts, _clean_html


# ── Instagram ─────────────────────────────────────────────────────────────


async def instagram(username: str) -> str:
    """
    Public Instagram profile via instaloader. No login required for public accounts.
    Extracts: bio, follower/following/post counts, external URL, verified status,
    business category, numeric user ID, tagged accounts + hashtags from recent posts.
    KEY PIVOT: external URL → domain chain; numeric user ID stable across renames;
    business category → org type.
    pip install instaloader
    """
    import instaloader

    def _fetch() -> str:
        L = instaloader.Instaloader()
        if getattr(config, "INSTAGRAM_USERNAME", None) and getattr(
            config, "INSTAGRAM_PASSWORD", None
        ):
            L.login(config.INSTAGRAM_USERNAME, config.INSTAGRAM_PASSWORD)
        try:
            profile = instaloader.Profile.from_username(L.context, username)
        except instaloader.exceptions.ProfileNotExistsException:
            return f"Instagram: profile '{username}' not found."
        except Exception as exc:
            return f"Instagram error: {exc}"

        lines = [
            f"Instagram profile: @{username}\n",
            f"Full name:     {profile.full_name or 'N/A'}",
            f"Bio:           {(profile.biography or 'N/A').replace(chr(10), ' ')}",
            f"External URL:  {profile.external_url or 'N/A'}",
            f"Posts:         {profile.mediacount:,}",
            f"Followers:     {profile.followers:,}",
            f"Following:     {profile.followees:,}",
            f"Verified:      {profile.is_verified}",
            f"Private:       {profile.is_private}",
            f"Business:      {profile.is_business_account}",
            f"Category:      {profile.business_category_name or 'N/A'}",
            f"User ID:       {profile.userid}",
            f"Profile URL:   https://www.instagram.com/{username}/",
        ]

        if not profile.is_private:
            try:
                import itertools

                tagged: set[str] = set()
                hashtags: set[str] = set()
                # islice stops the generator after 20 — never fetches the full post history
                for p in itertools.islice(profile.get_posts(), 20):
                    caption = p.caption or ""
                    tagged.update(re.findall(r"@([\w.]+)", caption))
                    hashtags.update(re.findall(r"#([\w]+)", caption))
                if tagged:
                    lines.append("\n── Tagged accounts (last 20 posts) ──")
                    for t in sorted(tagged)[:20]:
                        lines.append(f"  @{t}")
                if hashtags:
                    lines.append("\n── Top hashtags ──")
                    lines.append(
                        "  " + "  ".join(f"#{h}" for h in sorted(hashtags)[:20])
                    )
            except Exception:
                pass

        return "\n".join(lines)

    return await asyncio.to_thread(_fetch)


# ── Twitter / X ───────────────────────────────────────────────────────────


async def twitter(username: str) -> str:
    """
    Twitter/X profile via Twitter API v2 (bearer token — free tier).
    Extracts: bio, location, website, creation date, follower/following/tweet/listed
    counts, verified status, numeric user ID, pinned tweet text.
    KEY PIVOT: numeric user ID stable across handle changes; website → domain chain;
    creation date → account age / OPSEC timeline.
    Requires: TWITTER_BEARER_TOKEN
    """
    if not getattr(config, "TWITTER_BEARER_TOKEN", None):
        return "Twitter: TWITTER_BEARER_TOKEN not configured."

    hdrs = {"Authorization": f"Bearer {config.TWITTER_BEARER_TOKEN}"}
    try:
        await rate_limit("default")
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"https://api.twitter.com/2/users/by/username/{username}",
                headers=hdrs,
                params={
                    "user.fields": (
                        "id,name,description,location,url,created_at,"
                        "public_metrics,verified,entities,pinned_tweet_id,protected"
                    )
                },
            )
            data = r.json()
    except Exception as exc:
        return f"Twitter error: {exc}"

    if "errors" in data and "data" not in data:
        errs = "; ".join(e.get("detail", e.get("title", "?")) for e in data["errors"])
        return f"Twitter: {errs}"

    u = data.get("data", {})
    if not u:
        return f"Twitter: user '{username}' not found."

    m = u.get("public_metrics", {})
    entities = u.get("entities", {})
    urls = (entities.get("url") or {}).get("urls", [])
    exp_url = urls[0].get("expanded_url", "N/A") if urls else "N/A"

    lines = [
        f"Twitter/X profile: @{username}\n",
        f"Display name:  {u.get('name', 'N/A')}",
        f"Bio:           {(u.get('description', '') or 'N/A').replace(chr(10), ' ')}",
        f"Location:      {u.get('location', 'N/A') or 'N/A'}",
        f"Website:       {exp_url}",
        f"Created:       {u.get('created_at', 'N/A')}",
        f"Followers:     {m.get('followers_count', 0):,}",
        f"Following:     {m.get('following_count', 0):,}",
        f"Tweets:        {m.get('tweet_count', 0):,}",
        f"Listed:        {m.get('listed_count', 0):,}",
        f"Verified:      {u.get('verified', False)}",
        f"Protected:     {u.get('protected', False)}",
        f"User ID:       {u.get('id', 'N/A')}",
        f"Profile URL:   https://x.com/{username}",
    ]

    pinned_id = u.get("pinned_tweet_id")
    if pinned_id:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                tr = await client.get(
                    f"https://api.twitter.com/2/tweets/{pinned_id}",
                    headers=hdrs,
                    params={"tweet.fields": "text"},
                )
                td = tr.json().get("data", {})
                if td:
                    lines.append("\n── Pinned Tweet ──")
                    lines.append(
                        f"  {(td.get('text', '') or '').replace(chr(10), ' ')}"
                    )
        except Exception:
            pass

    return "\n".join(lines)


# ── TikTok ────────────────────────────────────────────────────────────────


async def tiktok(username: str) -> str:
    """
    TikTok profile via httpx parsing __UNIVERSAL_DATA_FOR_REHYDRATION__ JSON. No key.
    Extracts: nickname, bio, bio link, region, language, verified, private flag,
    numeric user ID, secUid (stable across renames), follower/following/like/video counts.
    KEY PIVOT: secUid stable across username changes; region → geo; bio link → domain chain.
    NOTE: TikTok bot-detection may block intermittently.
    """
    import json as _json

    url = f"https://www.tiktok.com/@{username}"
    try:
        await rate_limit("default")
        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            headers={
                "User-Agent": _BROWSER_UA,
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.tiktok.com/",
            },
        ) as client:
            r = await client.get(url)
            r.raise_for_status()
    except Exception as exc:
        return f"TikTok error: {exc}"

    match = re.search(
        r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
        r.text,
        re.DOTALL,
    )
    if not match:
        return f"TikTok: could not extract data for '@{username}' (bot-detection?)."

    try:
        raw = _json.loads(match.group(1))
        user_detail = (
            raw.get("__DEFAULT_SCOPE__", {})
            .get("webapp.user-detail", {})
            .get("userInfo", {})
        )
        user = user_detail.get("user", {})
        stats = user_detail.get("stats", {})
    except Exception:
        return f"TikTok: failed to parse embedded JSON for '@{username}'."

    if not user:
        return f"TikTok: profile '@{username}' not found."

    bl = user.get("bioLink", {})
    bio_link = bl.get("link", "") if isinstance(bl, dict) else ""

    lines = [
        f"TikTok profile: @{username}\n",
        f"Nickname:      {user.get('nickname', 'N/A')}",
        f"Bio:           {(user.get('signature', '') or 'N/A').replace(chr(10), ' ')}",
        f"Bio link:      {bio_link or 'N/A'}",
        f"Region:        {user.get('region', 'N/A') or 'N/A'}",
        f"Language:      {user.get('language', 'N/A') or 'N/A'}",
        f"Verified:      {bool(user.get('verified'))}",
        f"Private:       {bool(user.get('privateAccount'))}",
        f"User ID:       {user.get('id', 'N/A')}",
        f"Sec UID:       {user.get('secUid', 'N/A')}",
        f"Followers:     {stats.get('followerCount', 0):,}",
        f"Following:     {stats.get('followingCount', 0):,}",
        f"Total likes:   {stats.get('heartCount', 0):,}",
        f"Videos:        {stats.get('videoCount', 0):,}",
        f"Profile URL:   https://www.tiktok.com/@{username}",
    ]
    return "\n".join(lines)


# ── Bluesky ───────────────────────────────────────────────────────────────


async def bluesky(handle: str) -> str:
    """
    Bluesky profile via AT Protocol public API (app.bsky.actor.getProfile). No key.
    Extracts: display name, bio, DID (decentralised identity — stable across handle
    changes), follower/following/post counts, verification labels, creation date,
    linked PDS (personal data server).
    KEY PIVOT: DID is permanent and protocol-level — correlates identity even across
    handle renames; bio often contains cross-platform links.
    """
    handle = handle.lstrip("@")
    try:
        await rate_limit("default")
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile",
                params={"actor": handle},
            )
            if r.status_code == 400:
                return f"Bluesky: handle '{handle}' not found."
            r.raise_for_status()
            p = r.json()
    except Exception as exc:
        return f"Bluesky error: {exc}"

    labels = [lbl.get("val", "") for lbl in (p.get("labels") or [])]

    lines = [
        f"Bluesky profile: @{handle}\n",
        f"Display name:  {p.get('displayName', 'N/A') or 'N/A'}",
        f"Bio:           {(p.get('description', '') or 'N/A').replace(chr(10), ' ')}",
        f"DID:           {p.get('did', 'N/A')}",
        f"Followers:     {p.get('followersCount', 0):,}",
        f"Following:     {p.get('followsCount', 0):,}",
        f"Posts:         {p.get('postsCount', 0):,}",
        f"Verified:      {bool(labels)}",
        f"Labels:        {', '.join(labels) or 'N/A'}",
        f"PDS:           {p.get('associated', {}).get('pds', 'N/A')}",
        f"Profile URL:   https://bsky.app/profile/{handle}",
    ]
    return "\n".join(lines)


# ── Reddit ────────────────────────────────────────────────────────────────


async def reddit(username: str) -> str:
    """
    Reddit profile via public JSON API. No key required.
    Extracts: account creation date, post karma, comment karma, verified/gold/mod
    status, and recent post history with subreddit names, scores, and titles.
    KEY PIVOT: subreddit participation is a behavioral fingerprint —
    local city sub + local sports + local classifieds → strong geo signal;
    professional/technical subs → role and skill level hints;
    self-disclosure in post text (job, city, health) compounds over time.
    Also check: username mentions by other users (may reveal real identity),
    cross-post accounts in replies (may be alts), deleted posts (surrounding
    replies often reveal what was said).
    """
    username = username.strip().lstrip("u/")
    ua = getattr(config, "REDDIT_USER_AGENT", "osint-mcp/1.0")

    try:
        await rate_limit("default")
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"https://www.reddit.com/user/{username}/about.json",
                headers={"User-Agent": ua},
            )
            if r.status_code == 404:
                return f"Reddit: user '{username}' not found."
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        return f"Reddit error: {exc}"

    d = data.get("data", {})
    created = _ts(d.get("created_utc", 0))

    lines = [
        f"Reddit profile: u/{username}\n",
        f"Name:          {d.get('name', 'N/A')}",
        f"Created:       {created}",
        f"Post karma:    {d.get('link_karma', 0):,}",
        f"Comment karma: {d.get('comment_karma', 0):,}",
        f"Verified:      {d.get('verified', False)}",
        f"Premium:       {d.get('is_gold', False)}",
        f"Moderator:     {d.get('is_mod', False)}",
        f"NSFW:          {d.get('over_18', False)}",
        f"Profile URL:   https://reddit.com/u/{username}",
    ]

    try:
        await rate_limit("default")
        async with httpx.AsyncClient(timeout=15) as client:
            pr = await client.get(
                f"https://www.reddit.com/user/{username}/submitted.json",
                headers={"User-Agent": ua},
                params={"limit": 25},
            )
            post_list = (
                pr.json().get("data", {}).get("children", [])
                if pr.status_code == 200
                else []
            )

        if post_list:
            subreddits: set[str] = set()
            lines.append("\n── Recent Posts ──")
            for p in post_list:
                pd = p.get("data", {})
                subreddits.add(pd.get("subreddit", ""))
                lines.append(
                    f"  r/{pd.get('subreddit', '?'):20} "
                    f"↑{pd.get('score', 0):5}  "
                    f"{pd.get('title', '')[:80]}"
                )
            lines.append(f"\nActive subreddits: {', '.join(sorted(subreddits))}")
    except Exception:
        pass

    return "\n".join(lines)


# ── VK ────────────────────────────────────────────────────────────────────


async def vk(username: str) -> str:
    """
    VK profile via public API v5.131. No key required for public profiles;
    VK_ACCESS_TOKEN (optional) unlocks additional fields.
    Extracts: full name, screen name, status, sex, birth date, city, country,
    home town, university, occupation, website, follower count, last seen timestamp,
    verified status, Skype/Twitter/Instagram/Facebook cross-links.
    KEY PIVOT: richest biographical source for Russian/Eastern-European subjects;
    bdate + education + occupation = full life timeline; last_seen → timezone.
    Optional: VK_ACCESS_TOKEN
    """
    params: dict = {"v": "5.131"}
    if getattr(config, "VK_ACCESS_TOKEN", None):
        params["access_token"] = config.VK_ACCESS_TOKEN

    try:
        await rate_limit("default")
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://api.vk.com/method/users.get",
                params={
                    **params,
                    "user_ids": username,
                    "fields": (
                        "screen_name,bdate,city,country,home_town,"
                        "education,universities,schools,occupation,"
                        "personal,relation,followers_count,"
                        "connections,site,status,last_seen,sex,"
                        "verified,blacklisted,twitter,instagram,skype,facebook"
                    ),
                },
            )
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        return f"VK error: {exc}"

    if data.get("error"):
        return f"VK error: {data['error'].get('error_msg', 'unknown')}"

    users = data.get("response", [])
    if not users:
        return f"VK: user '{username}' not found."
    u = users[0]

    if u.get("deactivated"):
        return f"VK: account '{username}' is {u['deactivated']}."

    city = (
        u.get("city", {}).get("title", "N/A")
        if isinstance(u.get("city"), dict)
        else "N/A"
    )
    country = (
        u.get("country", {}).get("title", "N/A")
        if isinstance(u.get("country"), dict)
        else "N/A"
    )
    unis = u.get("universities", [])
    university = (
        f"{unis[0].get('name', '?')} (grad {unis[0].get('graduation', '?')})"
        if unis
        else "N/A"
    )
    occ = u.get("occupation", {})
    occupation = f"{occ.get('name', '?')} [{occ.get('type', '?')}]" if occ else "N/A"
    ls_ts = (
        u.get("last_seen", {}).get("time", 0)
        if isinstance(u.get("last_seen"), dict)
        else 0
    )
    last_seen = (
        datetime.datetime.fromtimestamp(ls_ts).strftime("%Y-%m-%d %H:%M")
        if ls_ts
        else "N/A"
    )

    sex_map = {0: "N/A", 1: "Female", 2: "Male"}
    lines = [
        f"VK profile: {username}\n",
        f"Name:          {u.get('first_name', '')} {u.get('last_name', '')}".strip(),
        f"Screen name:   {u.get('screen_name', 'N/A')}",
        f"Status:        {u.get('status', 'N/A') or 'N/A'}",
        f"Sex:           {sex_map.get(u.get('sex', 0), 'N/A')}",
        f"Birth date:    {u.get('bdate', 'N/A') or 'N/A'}",
        f"City:          {city}",
        f"Country:       {country}",
        f"Home town:     {u.get('home_town', 'N/A') or 'N/A'}",
        f"University:    {university}",
        f"Occupation:    {occupation}",
        f"Website:       {u.get('site', 'N/A') or 'N/A'}",
        f"Followers:     {u.get('followers_count', 0):,}",
        f"Verified:      {bool(u.get('verified'))}",
        f"Last seen:     {last_seen}",
        f"User ID:       {u.get('id', 'N/A')}",
        f"Profile URL:   https://vk.com/{u.get('screen_name', username)}",
    ]

    for key in ("skype", "twitter", "instagram", "facebook"):
        val = u.get(key) or (u.get("connections") or {}).get(key)
        if val:
            lines.append(f"{key.title():14} {val}")

    return "\n".join(lines)


# ── Tumblr ────────────────────────────────────────────────────────────────


async def tumblr(blogname: str) -> str:
    """
    Tumblr blog via official API v2. API key required.
    Extracts: blog title, description, post count, follower count (if available),
    blog age, avatar URL, linked theme/URL, and recent post titles/tags.
    KEY PIVOT: description often contains real name or contact info; post tags →
    interest fingerprint; post history timestamps → activity pattern.
    Requires: TUMBLR_API_KEY
    """
    key = getattr(config, "TUMBLR_API_KEY", None)
    if not key:
        return "Tumblr: TUMBLR_API_KEY not configured."

    try:
        await rate_limit("default")
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"https://api.tumblr.com/v2/blog/{blogname}/info",
                params={"api_key": key},
            )
            if r.status_code == 404:
                return f"Tumblr: blog '{blogname}' not found."
            r.raise_for_status()
            blog = r.json().get("response", {}).get("blog", {})

            posts_r = await client.get(
                f"https://api.tumblr.com/v2/blog/{blogname}/posts",
                params={"api_key": key, "limit": 10},
            )
            posts = (
                posts_r.json().get("response", {}).get("posts", [])
                if posts_r.status_code == 200
                else []
            )
    except Exception as exc:
        return f"Tumblr error: {exc}"

    if not blog:
        return f"Tumblr: blog '{blogname}' not found."

    lines = [
        f"Tumblr blog: {blogname}\n",
        f"Title:         {blog.get('title', 'N/A') or 'N/A'}",
        f"Description:   {_clean_html(blog.get('description', '') or '') or 'N/A'}",
        f"Posts:         {blog.get('posts', 0):,}",
        f"Likes:         {blog.get('likes', 'N/A')}",
        f"Ask enabled:   {bool(blog.get('ask'))}",
        f"NSFW:          {bool(blog.get('is_nsfw'))}",
        f"Active:        {bool(blog.get('active'))}",
        f"Updated:       {_ts(blog.get('updated', 0))}",
        f"Profile URL:   https://{blogname}.tumblr.com",
    ]

    all_tags: list[str] = []
    if posts:
        lines.append(f"\n── Recent Posts ({len(posts)}) ──")
        for post in posts[:10]:
            title = post.get("title") or post.get("summary", "")[:60] or "(untitled)"
            tags = post.get("tags", [])
            all_tags.extend(tags)
            lines.append(f"  {title[:60]:62} [{', '.join(tags[:3])}]")

    if all_tags:
        from collections import Counter

        top = Counter(all_tags).most_common(8)
        lines.append("\n── Top Tags ──")
        lines.append("  " + "  ".join(f"{tag}({cnt})" for tag, cnt in top))

    return "\n".join(lines)
