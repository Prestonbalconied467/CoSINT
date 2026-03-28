"""
tools/social/gaming.py  –  Gaming platform handlers.

Platforms (no API key):
  steamcommunity.com → steam()        [community XML API, vanity URL]
                     → steam_by_id()  [community XML API, Steam64 ID]
  chess.com          → chess()        [Chess.com public API]
  lichess.org        → lichess()      [Lichess public API]
"""

import xml.etree.ElementTree as ET

import httpx

from shared.rate_limiter import rate_limit
from ._helpers import _BROWSER_UA, _ts


# ── Steam ─────────────────────────────────────────────────────────────────


def _parse_steam_xml(root: ET.Element, identifier: str) -> str:
    def t(tag: str) -> str:
        el = root.find(tag)
        return (el.text or "").strip() if el is not None else "N/A"

    err = root.find("error")
    if err is not None:
        return f"Steam: {err.text} ('{identifier}')"

    most_played: list[tuple[str, str]] = []
    for game in root.findall(".//mostPlayedGames/mostPlayedGame"):
        name_el = game.find("gameName")
        hrs_el = game.find("hoursOnRecord")
        if name_el is not None and name_el.text:
            most_played.append(
                (name_el.text.strip(), hrs_el.text if hrs_el is not None else "?")
            )

    groups: list[tuple[str, str]] = []
    for grp in root.findall(".//groups/group"):
        name_el = grp.find("groupName")
        url_el = grp.find("groupURL")
        if name_el is not None and name_el.text:
            groups.append(
                (
                    name_el.text.strip(),
                    url_el.text.strip() if url_el is not None else "",
                )
            )

    lines = [
        f"Steam profile: {t('customURL') or identifier}\n",
        f"Display name:  {t('steamID')}",
        f"Real name:     {t('realname')}",
        f"Location:      {t('location')}",
        f"Status:        {t('onlineState')}",
        f"Member since:  {t('memberSince')}",
        f"Privacy:       {t('privacyState')}",
        f"VAC banned:    {t('vacBanned')}",
        f"Trade ban:     {t('tradeBanState')}",
        f"Limited acct:  {t('isLimitedAccount')}",
        f"Summary:       {t('summary')}",
        f"Steam64 ID:    {t('steamID64')}",
        f"Profile URL:   https://steamcommunity.com/id/{t('customURL') or identifier}",
    ]

    if most_played:
        lines.append("\n── Most Played Games ──")
        for name, hrs in most_played[:8]:
            lines.append(f"  {name} {hrs} hrs")
    if groups:
        lines.append(f"\n── Groups ({len(groups)}) ──")
        for name, grp_url in groups:
            lines.append(f"  {name} https://steamcommunity.com/groups/{grp_url}")

    return "\n".join(lines)


async def steam(vanity: str) -> str:
    """
    Steam profile via community XML API (vanity URL slug).
    Extracts: display name, real name, location, VAC/trade bans, limited account flag,
    bio, Steam64 ID, most played games, group memberships.
    KEY PIVOT: real name often set early; groups → community / geo signals.
    """
    try:
        await rate_limit("default")
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(
                f"https://steamcommunity.com/id/{vanity}/?xml=1",
                headers={"User-Agent": _BROWSER_UA},
            )
            r.raise_for_status()
        return _parse_steam_xml(ET.fromstring(r.text), vanity)
    except ET.ParseError:
        return f"Steam: could not parse XML for '{vanity}'."
    except Exception as exc:
        return f"Steam error: {exc}"


async def steam_by_id(steam64_id: str) -> str:
    """Steam profile via numeric 64-bit SteamID (/profiles/<id> URLs)."""
    try:
        await rate_limit("default")
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(
                f"https://steamcommunity.com/profiles/{steam64_id}/?xml=1",
                headers={"User-Agent": _BROWSER_UA},
            )
            r.raise_for_status()
        return _parse_steam_xml(ET.fromstring(r.text), steam64_id)
    except ET.ParseError:
        return f"Steam: could not parse XML for ID '{steam64_id}'."
    except Exception as exc:
        return f"Steam error: {exc}"


# ── Chess.com ─────────────────────────────────────────────────────────────


async def chess(username: str) -> str:
    """
    Chess.com profile via public API. No key required.
    Extracts: real name, location, country, title (GM/IM/FM etc.), verified status,
    last online timestamp, account creation date, status (premium/staff), streaming
    flag, and current ratings across all time controls.
    KEY PIVOT: real name field is frequently set; country → geo pin; last online
    timestamp → activity pattern / timezone inference.
    """
    try:
        await rate_limit("default")
        async with httpx.AsyncClient(timeout=15) as client:
            prof_r = await client.get(f"https://api.chess.com/pub/player/{username}")
            if prof_r.status_code == 404:
                return f"Chess.com: user '{username}' not found."
            prof_r.raise_for_status()
            profile = prof_r.json()

            stats_r = await client.get(
                f"https://api.chess.com/pub/player/{username}/stats"
            )
            stats = stats_r.json() if stats_r.status_code == 200 else {}
    except Exception as exc:
        return f"Chess.com error: {exc}"

    country_code = (
        profile.get("country", "").split("/")[-1] if profile.get("country") else "N/A"
    )

    lines = [
        f"Chess.com profile: {username}\n",
        f"Real name:     {profile.get('name', 'N/A') or 'N/A'}",
        f"Username:      {profile.get('username', 'N/A')}",
        f"Title:         {profile.get('title', 'N/A') or 'N/A'}",
        f"Location:      {profile.get('location', 'N/A') or 'N/A'}",
        f"Country:       {country_code}",
        f"Verified:      {bool(profile.get('verified'))}",
        f"Status:        {profile.get('status', 'N/A')}",
        f"Streamer:      {bool(profile.get('is_streamer'))}",
        f"Last online:   {_ts(profile.get('last_online', 0))}",
        f"Joined:        {_ts(profile.get('joined', 0))}",
        f"Profile URL:   {profile.get('url', f'https://www.chess.com/member/{username}')}",
    ]

    rating_keys = {
        "chess_bullet": "Bullet",
        "chess_blitz": "Blitz",
        "chess_rapid": "Rapid",
        "chess_daily": "Daily",
        "chess960_daily": "Daily 960",
        "tactics": "Tactics",
        "puzzle_rush": "Puzzle Rush",
    }
    rating_lines = []
    for key, label in rating_keys.items():
        data = stats.get(key, {})
        if data:
            last = data.get("last", {}) or data.get("best", {}) or {}
            rating = last.get("rating", "?")
            rating_lines.append(f"  {label} {rating}")
    if rating_lines:
        lines.append("\n── Ratings ──")
        lines.extend(rating_lines)

    return "\n".join(lines)


# ── Lichess ───────────────────────────────────────────────────────────────


async def lichess(username: str) -> str:
    """
    Lichess profile via public API. No key required.
    Extracts: username, title, bio, country, creation date, last seen, online status,
    ratings across all time controls, number of games played per variant, streaming
    flag, and patron status.
    KEY PIVOT: country field → geo pin; creation date → account timeline.
    """
    try:
        await rate_limit("default")
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"https://lichess.org/api/user/{username}",
                headers={"Accept": "application/json"},
            )
            if r.status_code == 404:
                return f"Lichess: user '{username}' not found."
            r.raise_for_status()
            user = r.json()
    except Exception as exc:
        return f"Lichess error: {exc}"

    profile = user.get("profile", {}) or {}
    perfs = user.get("perfs", {}) or {}
    play_time = user.get("playTime", {}) or {}

    country = profile.get("country", "N/A") or "N/A"
    bio = profile.get("bio", "") or "N/A"

    lines = [
        f"Lichess profile: {username}\n",
        f"Username:      {user.get('username', 'N/A')}",
        f"Title:         {user.get('title', 'N/A') or 'N/A'}",
        f"Bio:           {bio}",
        f"Country:       {country}",
        f"Real name:     {profile.get('realName', 'N/A') or 'N/A'}",
        f"FIDE rating:   {profile.get('fideRating', 'N/A') or 'N/A'}",
        f"Links:         {profile.get('links', 'N/A') or 'N/A'}",
        f"Patron:        {bool(user.get('patron'))}",
        f"Streamer:      {bool(user.get('streaming'))}",
        f"Online:        {bool(user.get('online'))}",
        f"Created:       {_ts(user.get('createdAt', 0) / 1000)}",
        f"Last seen:     {_ts(user.get('seenAt', 0) / 1000)}",
        f"Play time:     {play_time.get('total', 0) // 3600}h total",
        f"Profile URL:   https://lichess.org/@/{username}",
    ]

    perf_keys = ["bullet", "blitz", "rapid", "classical", "correspondence", "puzzle"]
    perf_lines = []
    for key in perf_keys:
        p = perfs.get(key, {})
        if p and p.get("games", 0) > 0:
            perf_lines.append(
                f"  {key} rating: {p.get('rating', '?')}  games: {p.get('games', 0):,}"
            )
    if perf_lines:
        lines.append("\n── Ratings ──")
        lines.extend(perf_lines)

    return "\n".join(lines)
