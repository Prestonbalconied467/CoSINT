"""
tools/social/media.py  –  Media & entertainment platform handlers.

Platforms (no API key):
  medium.com     → medium()    [HTML scrape]

Platforms (API key required):
  youtube.com    → youtube(), youtube_by_channel_id()  [YOUTUBE_API_KEY]
  twitch.tv      → twitch()    [TWITCH_CLIENT_ID + TWITCH_CLIENT_SECRET]
  open.spotify.com → spotify() [SPOTIFY_CLIENT_ID + SPOTIFY_CLIENT_SECRET]
  last.fm        → lastfm()    [LASTFM_API_KEY]
  soundcloud.com → soundcloud() [SOUNDCLOUD_CLIENT_ID]
  flickr.com     → flickr()   [FLICKR_API_KEY]
"""

import base64
import re

import httpx

from shared import config
from shared.rate_limiter import rate_limit
from ._helpers import _BROWSER_UA, _ts, _clean_html


# ── YouTube ───────────────────────────────────────────────────────────────


async def youtube(handle_or_username: str) -> str:
    """
    YouTube channel via Data API v3. API key only — no OAuth.
    Extracts: channel title, description, country, creation date, subscriber count,
    total views, video count, stable channel ID, branding keywords, topic categories,
    recent video titles + URLs.
    KEY PIVOT: country → hard geo pin; channel ID stable across handle changes;
    keywords → interest fingerprint.
    Requires: YOUTUBE_API_KEY
    """
    key = getattr(config, "YOUTUBE_API_KEY", None)
    if not key:
        return "YouTube: YOUTUBE_API_KEY not configured."

    fields = "snippet,statistics,brandingSettings,topicDetails"
    try:
        await rate_limit("default")
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={"part": fields, "forHandle": handle_or_username, "key": key},
            )
            items = r.json().get("items", [])

            if not items:
                r2 = await client.get(
                    "https://www.googleapis.com/youtube/v3/channels",
                    params={
                        "part": fields,
                        "forUsername": handle_or_username,
                        "key": key,
                    },
                )
                items = r2.json().get("items", [])

            if not items:
                sr = await client.get(
                    "https://www.googleapis.com/youtube/v3/search",
                    params={
                        "part": "snippet",
                        "q": handle_or_username,
                        "type": "channel",
                        "key": key,
                        "maxResults": 1,
                    },
                )
                hits = sr.json().get("items", [])
                if hits:
                    cid = hits[0]["id"]["channelId"]
                    r3 = await client.get(
                        "https://www.googleapis.com/youtube/v3/channels",
                        params={"part": fields, "id": cid, "key": key},
                    )
                    items = r3.json().get("items", [])

            if not items:
                return f"YouTube: channel '{handle_or_username}' not found."

            ch = items[0]
            channel_id = ch["id"]
            snippet = ch.get("snippet", {})
            stats = ch.get("statistics", {})
            branding = ch.get("brandingSettings", {}).get("channel", {})
            topics = ch.get("topicDetails", {}).get("topicCategories", [])

            up_r = await client.get(
                "https://www.googleapis.com/youtube/v3/playlistItems",
                params={
                    "part": "snippet",
                    "playlistId": "UU" + channel_id[2:],
                    "key": key,
                    "maxResults": 5,
                },
            )
            uploads = up_r.json().get("items", []) if up_r.status_code == 200 else []
    except Exception as exc:
        return f"YouTube error: {exc}"

    hidden_subs = stats.get("hiddenSubscriberCount", False)
    sub_count = "hidden" if hidden_subs else f"{int(stats.get('subscriberCount', 0)):,}"

    lines = [
        f"YouTube channel: {snippet.get('customUrl', handle_or_username)}\n",
        f"Name:          {snippet.get('title', 'N/A')}",
        f"Description:   {(snippet.get('description', '') or 'N/A').replace(chr(10), ' ')[:200]}",
        f"Country:       {snippet.get('country', 'N/A') or 'N/A'}",
        f"Created:       {snippet.get('publishedAt', 'N/A')}",
        f"Subscribers:   {sub_count}",
        f"Total views:   {int(stats.get('viewCount', 0)):,}",
        f"Videos:        {stats.get('videoCount', 'N/A')}",
        f"Channel ID:    {channel_id}",
        f"Keywords:      {(branding.get('keywords', '') or 'N/A')[:120]}",
        f"Profile URL:   https://youtube.com/channel/{channel_id}",
    ]

    if topics:
        lines.append(
            f"Topics:        {', '.join(t.split('/')[-1].replace('_', ' ') for t in topics)}"
        )

    if uploads:
        lines.append("\n── Recent Videos ──")
        for v in uploads:
            snip = v.get("snippet", {})
            vid_id = snip.get("resourceId", {}).get("videoId", "?")
            lines.append(f"  {snip.get('title', '?')[:60]:62}https://youtu.be/{vid_id}")

    return "\n".join(lines)


async def youtube_by_channel_id(channel_id: str) -> str:
    """YouTube channel lookup by /channel/UC... ID, delegates to youtube()."""
    key = getattr(config, "YOUTUBE_API_KEY", None)
    if not key:
        return "YouTube: YOUTUBE_API_KEY not configured."
    try:
        await rate_limit("default")
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={"part": "snippet", "id": channel_id, "key": key},
            )
            items = r.json().get("items", [])
            if not items:
                return f"YouTube: channel ID '{channel_id}' not found."
            handle = (
                items[0].get("snippet", {}).get("customUrl", channel_id).lstrip("@")
            )
        return await youtube(handle)
    except Exception as exc:
        return f"YouTube error: {exc}"


# ── Twitch ────────────────────────────────────────────────────────────────


async def twitch(username: str) -> str:
    """
    Twitch profile via Helix API (client credentials OAuth — no user auth needed).
    Extracts: display name, bio, broadcaster type, creation date, user ID, follower
    count, current game/category, stream title, language, tags, recent clips.
    KEY PIVOT: stream language → geo; bio → may contain Discord / social handles.
    Requires: TWITCH_CLIENT_ID + TWITCH_CLIENT_SECRET
    """
    cid = getattr(config, "TWITCH_CLIENT_ID", None)
    csec = getattr(config, "TWITCH_CLIENT_SECRET", None)
    if not cid or not csec:
        return "Twitch: TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET not configured."

    try:
        await rate_limit("default")
        async with httpx.AsyncClient(timeout=15) as client:
            tok_r = await client.post(
                "https://id.twitch.tv/oauth2/token",
                params={
                    "client_id": cid,
                    "client_secret": csec,
                    "grant_type": "client_credentials",
                },
            )
            tok_r.raise_for_status()
            token = tok_r.json()["access_token"]
            hdrs = {"Client-Id": cid, "Authorization": f"Bearer {token}"}

            user_r = await client.get(
                "https://api.twitch.tv/helix/users",
                params={"login": username},
                headers=hdrs,
            )
            user_r.raise_for_status()
            users = user_r.json().get("data", [])
            if not users:
                return f"Twitch: user '{username}' not found."
            user = users[0]
            uid = user["id"]

            ch_r = await client.get(
                "https://api.twitch.tv/helix/channels",
                params={"broadcaster_id": uid},
                headers=hdrs,
            )
            channel = (
                ch_r.json().get("data", [{}])[0] if ch_r.status_code == 200 else {}
            )

            fol_r = await client.get(
                "https://api.twitch.tv/helix/channels/followers",
                params={"broadcaster_id": uid},
                headers=hdrs,
            )
            followers = (
                fol_r.json().get("total", "N/A") if fol_r.status_code == 200 else "N/A"
            )

            clips_r = await client.get(
                "https://api.twitch.tv/helix/clips",
                params={"broadcaster_id": uid, "first": 5},
                headers=hdrs,
            )
            clips = clips_r.json().get("data", []) if clips_r.status_code == 200 else []
    except Exception as exc:
        return f"Twitch error: {exc}"

    lines = [
        f"Twitch profile: {username}\n",
        f"Display name:  {user.get('display_name', 'N/A')}",
        f"Bio:           {(user.get('description', '') or 'N/A').replace(chr(10), ' ')[:200]}",
        f"Type:          {user.get('broadcaster_type', '') or 'regular'}",
        f"Created:       {user.get('created_at', 'N/A')}",
        f"User ID:       {uid}",
        f"Followers:     {followers:,}"
        if isinstance(followers, int)
        else f"Followers:     {followers}",
        f"Profile URL:   https://twitch.tv/{username}",
    ]

    if channel:
        lines += [
            "\n── Channel ──",
            f"  Game:        {channel.get('game_name', 'N/A') or 'N/A'}",
            f"  Title:       {(channel.get('title', '') or 'N/A')[:80]}",
            f"  Language:    {channel.get('broadcaster_language', 'N/A')}",
            f"  Tags:        {', '.join(channel.get('tags', [])) or 'N/A'}",
        ]

    if clips:
        lines.append("\n── Recent Clips ──")
        for c in clips:
            lines.append(
                f"  {c.get('title', '?')[:50]:52} 👁 {c.get('view_count', 0):,}  {c.get('url', '')}"
            )

    return "\n".join(lines)


# ── Spotify ───────────────────────────────────────────────────────────────


async def spotify(user_id: str) -> str:
    """
    Spotify public profile via Web API (client credentials OAuth — no user auth).
    Extracts: display name, follower count, public playlists with track counts.
    KEY PIVOT: playlist names are a reliable interest/persona fingerprint — often
    contain real names, location references, or temporal markers.
    Requires: SPOTIFY_CLIENT_ID + SPOTIFY_CLIENT_SECRET
    """
    cid = getattr(config, "SPOTIFY_CLIENT_ID", None)
    csec = getattr(config, "SPOTIFY_CLIENT_SECRET", None)
    if not cid or not csec:
        return "Spotify: SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET not configured."

    creds_b64 = base64.b64encode(f"{cid}:{csec}".encode()).decode()
    try:
        await rate_limit("default")
        async with httpx.AsyncClient(timeout=15) as client:
            tok_r = await client.post(
                "https://accounts.spotify.com/api/token",
                headers={
                    "Authorization": f"Basic {creds_b64}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"grant_type": "client_credentials"},
            )
            tok_r.raise_for_status()
            hdrs = {"Authorization": f"Bearer {tok_r.json()['access_token']}"}

            prof_r = await client.get(
                f"https://api.spotify.com/v1/users/{user_id}", headers=hdrs
            )
            if prof_r.status_code == 404:
                return f"Spotify: user '{user_id}' not found."
            prof_r.raise_for_status()
            profile = prof_r.json()

            pl_r = await client.get(
                f"https://api.spotify.com/v1/users/{user_id}/playlists",
                headers=hdrs,
                params={"limit": 20},
            )
            playlists = pl_r.json().get("items", []) if pl_r.status_code == 200 else []
    except Exception as exc:
        return f"Spotify error: {exc}"

    spot_url = profile.get("external_urls", {}).get(
        "spotify", f"https://open.spotify.com/user/{user_id}"
    )

    lines = [
        f"Spotify profile: {user_id}\n",
        f"Display name:  {profile.get('display_name', 'N/A') or 'N/A'}",
        f"Followers:     {profile.get('followers', {}).get('total', 0):,}",
        f"Profile URL:   {spot_url}",
    ]

    active = [pl for pl in playlists if pl]
    if active:
        lines.append(f"\n── Public Playlists ({len(active)}) ──")
        for pl in active:
            pl_url = pl.get("external_urls", {}).get("spotify", "")
            lines.append(
                f"  {pl.get('name', '?'):40} {pl.get('tracks', {}).get('total', 0):3} tracks  {pl_url}"
            )
    else:
        lines.append("\nNo public playlists found.")

    return "\n".join(lines)


# ── Last.fm ───────────────────────────────────────────────────────────────


async def lastfm(username: str) -> str:
    """
    Last.fm profile via official API. API key required.
    Extracts: real name, country, age, subscriber status, play count, artist count,
    account creation date, top artists (= interest/taste fingerprint), top tracks,
    and recent tracks with timestamps (= activity pattern / timezone inference).
    KEY PIVOT: top artists are an extremely reliable interest fingerprint; timestamps
    on recent tracks → activity hours → likely timezone.
    Requires: LASTFM_API_KEY
    """
    key = getattr(config, "LASTFM_API_KEY", None)
    if not key:
        return "Last.fm: LASTFM_API_KEY not configured."

    base = "https://ws.audioscrobbler.com/2.0/"
    try:
        await rate_limit("default")
        async with httpx.AsyncClient(timeout=15) as client:

            def p(method: str, **kwargs):
                return {
                    "method": method,
                    "user": username,
                    "api_key": key,
                    "format": "json",
                    **kwargs,
                }

            info_r = await client.get(base, params=p("user.getinfo"))
            top_art_r = await client.get(
                base, params=p("user.gettopartists", limit=10, period="overall")
            )
            recent_r = await client.get(
                base, params=p("user.getrecenttracks", limit=10)
            )
    except Exception as exc:
        return f"Last.fm error: {exc}"

    if info_r.status_code != 200:
        return f"Last.fm: user '{username}' not found or API error."

    user = info_r.json().get("user", {})
    if not user:
        return f"Last.fm: user '{username}' not found."

    lines = [
        f"Last.fm profile: {username}\n",
        f"Real name:     {user.get('realname', 'N/A') or 'N/A'}",
        f"Country:       {user.get('country', 'N/A') or 'N/A'}",
        f"Age:           {user.get('age', 'N/A') or 'N/A'}",
        f"Gender:        {user.get('gender', 'N/A') or 'N/A'}",
        f"Subscriber:    {user.get('subscriber', '0') == '1'}",
        f"Play count:    {int(user.get('playcount', 0)):,}",
        f"Artist count:  {int(user.get('artist_count', 0)):,}",
        f"Track count:   {int(user.get('track_count', 0)):,}",
        f"Album count:   {int(user.get('album_count', 0)):,}",
        f"Registered:    {_ts(int(user.get('registered', {}).get('unixtime', 0)))}",
        f"Profile URL:   {user.get('url', f'https://www.last.fm/user/{username}')}",
    ]

    top_data = (
        top_art_r.json().get("topartists", {}).get("artist", [])
        if top_art_r.status_code == 200
        else []
    )
    if top_data:
        lines.append("\n── Top Artists (All Time) ──")
        for a in top_data[:10]:
            lines.append(
                f"  {a.get('name', '?'):30} {int(a.get('playcount', 0)):,} plays"
            )

    recent_data = (
        recent_r.json().get("recenttracks", {}).get("track", [])
        if recent_r.status_code == 200
        else []
    )
    if recent_data:
        lines.append("\n── Recent Tracks ──")
        for t in recent_data[:10]:
            date_info = t.get("date", {})
            date_str = (
                date_info.get("#text", "now playing") if date_info else "now playing"
            )
            artist = (t.get("artist", {}) or {}).get("#text", "?")
            track = t.get("name", "?")
            lines.append(f"  {date_str:20} {artist:25} – {track[:40]}")

    return "\n".join(lines)


# ── SoundCloud ────────────────────────────────────────────────────────────


async def soundcloud(username: str) -> str:
    """
    SoundCloud profile via API. Client ID required (free, obtained from SC app).
    Extracts: full name, bio, city, country, website, follower/following/track counts,
    verified status, creation date, and track list with play counts.
    KEY PIVOT: website → domain chain; city + country → geo pin; track descriptions
    may contain contact info.
    Requires: SOUNDCLOUD_CLIENT_ID
    """
    cid = getattr(config, "SOUNDCLOUD_CLIENT_ID", None)
    if not cid:
        return "SoundCloud: SOUNDCLOUD_CLIENT_ID not configured."

    try:
        await rate_limit("default")
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://api-v2.soundcloud.com/search/users",
                params={"q": username, "limit": 1, "client_id": cid},
            )
            r.raise_for_status()
            results = r.json().get("collection", [])
            if not results:
                return f"SoundCloud: user '{username}' not found."
            user = next(
                (
                    u
                    for u in results
                    if u.get("permalink", "").lower() == username.lower()
                ),
                results[0],
            )
            uid = user["id"]

            tracks_r = await client.get(
                f"https://api-v2.soundcloud.com/users/{uid}/tracks",
                params={"limit": 10, "client_id": cid},
            )
            tracks = (
                tracks_r.json().get("collection", [])
                if tracks_r.status_code == 200
                else []
            )
    except Exception as exc:
        return f"SoundCloud error: {exc}"

    lines = [
        f"SoundCloud profile: {username}\n",
        f"Full name:     {user.get('full_name', 'N/A') or 'N/A'}",
        f"Bio:           {(user.get('description', '') or 'N/A').replace(chr(10), ' ')[:200]}",
        f"City:          {user.get('city', 'N/A') or 'N/A'}",
        f"Country:       {user.get('country_code', 'N/A') or 'N/A'}",
        f"Website:       {user.get('website', 'N/A') or 'N/A'}",
        f"Website title: {user.get('website_title', 'N/A') or 'N/A'}",
        f"Followers:     {user.get('followers_count', 0):,}",
        f"Following:     {user.get('followings_count', 0):,}",
        f"Tracks:        {user.get('track_count', 0):,}",
        f"Verified:      {bool(user.get('verified'))}",
        f"Created:       {(user.get('created_at', 'N/A') or 'N/A')[:10]}",
        f"User ID:       {uid}",
        f"Profile URL:   {user.get('permalink_url', f'https://soundcloud.com/{username}')}",
    ]

    if tracks:
        lines.append(f"\n── Tracks ({len(tracks)}) ──")
        for t in tracks:
            plays = t.get("playback_count", 0) or 0
            lines.append(f"  {t.get('title', '?')[:50]:52} ▶ {plays:,}")

    return "\n".join(lines)


# ── Flickr ────────────────────────────────────────────────────────────────


async def flickr(user_id_or_nsid: str) -> str:
    """
    Flickr profile via public API. API key required.
    Extracts: real name, username, bio, location, photo count, pro status, creation
    date, public groups (= community/interest fingerprint), and public albums.
    KEY PIVOT: real name field is often set; location → geo pin; groups → strong
    interest/community fingerprint; EXIF data in photos may contain GPS coordinates
    (fetch individual photo details for geo OSINT).
    Requires: FLICKR_API_KEY
    """
    key = getattr(config, "FLICKR_API_KEY", None)
    if not key:
        return "Flickr: FLICKR_API_KEY not configured."

    base = "https://www.flickr.com/services/rest/"

    async def call(method: str, **kwargs) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                base,
                params={
                    "method": method,
                    "api_key": key,
                    "format": "json",
                    "nojsoncallback": 1,
                    **kwargs,
                },
            )
            r.raise_for_status()
            return r.json()

    try:
        await rate_limit("default")

        if not re.match(r"^\d+@N\d+$", user_id_or_nsid):
            lookup = await call(
                "flickr.urls.lookupUser",
                url=f"https://www.flickr.com/people/{user_id_or_nsid}",
            )
            nsid = lookup.get("user", {}).get("id", user_id_or_nsid)
        else:
            nsid = user_id_or_nsid

        info = await call("flickr.people.getInfo", user_id=nsid)
        groups = await call("flickr.people.getPublicGroups", user_id=nsid)
        albums = await call("flickr.photosets.getList", user_id=nsid, per_page=10)
    except Exception as exc:
        return f"Flickr error: {exc}"

    person = info.get("person", {})
    if not person:
        return f"Flickr: user '{user_id_or_nsid}' not found."

    username = (person.get("username", {}) or {}).get("_content", "N/A")
    realname = (person.get("realname", {}) or {}).get("_content", "N/A") or "N/A"
    location = (person.get("location", {}) or {}).get("_content", "N/A") or "N/A"
    description = _clean_html(
        (person.get("description", {}) or {}).get("_content", "") or ""
    )
    photos_count = (person.get("photos", {}) or {}).get("count", {}).get("_content", 0)

    lines = [
        f"Flickr profile: {username}\n",
        f"Username:      {username}",
        f"Real name:     {realname}",
        f"Location:      {location}",
        f"Bio:           {description[:200] or 'N/A'}",
        f"Photos:        {int(photos_count):,}",
        f"Pro:           {bool(person.get('ispro'))}",
        f"NSID:          {nsid}",
        f"Profile URL:   https://www.flickr.com/people/{nsid}/",
    ]

    group_list = groups.get("groups", {}).get("group", [])
    if group_list:
        lines.append(f"\n── Public Groups ({len(group_list)}) ──")
        for g in group_list[:10]:
            lines.append(f"  {g.get('name', '?')}")

    album_list = albums.get("photosets", {}).get("photoset", [])
    if album_list:
        lines.append(f"\n── Albums ({len(album_list)}) ──")
        for a in album_list[:8]:
            title = (a.get("title", {}) or {}).get("_content", "?")
            count = a.get("count_photos", 0)
            lines.append(f"  {title:40} {count} photos")

    return "\n".join(lines)


# ── Medium ────────────────────────────────────────────────────────────────


async def medium(username: str) -> str:
    """
    Medium profile via HTML scrape + embedded JSON-LD / meta tags. No key required.
    Extracts: display name, bio, follower count, article list with titles and tags.
    KEY PIVOT: article topics form a strong interest/professional fingerprint;
    bio often contains cross-platform links.
    """
    slug = username.lstrip("@")
    url = f"https://medium.com/@{slug}"
    try:
        await rate_limit("default")
        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": _BROWSER_UA},
        ) as client:
            r = await client.get(url)
            if r.status_code == 404:
                return f"Medium: profile '@{slug}' not found."
            r.raise_for_status()
    except Exception as exc:
        return f"Medium error: {exc}"

    html = r.text

    name_m = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    name = name_m.group(1).strip() if name_m else "N/A"

    desc_m = re.search(r'<meta name="description" content="([^"]+)"', html)
    bio = desc_m.group(1).strip()[:200] if desc_m else "N/A"

    followers = "N/A"
    fol_m = re.search(r'"followerCount"\s*:\s*(\d+)', html)
    if fol_m:
        followers = f"{int(fol_m.group(1)):,}"

    articles: list[str] = []
    for m in re.finditer(r'"title"\s*:\s*"([^"]{10,120})"', html):
        t = m.group(1).strip()
        if t and t not in articles and len(articles) < 10:
            articles.append(t)

    lines = [
        f"Medium profile: @{slug}\n",
        f"Name:          {name}",
        f"Bio:           {bio}",
        f"Followers:     {followers}",
        f"Profile URL:   {url}",
    ]

    if articles:
        lines.append(f"\n── Recent Articles ({len(articles)}) ──")
        for title in articles:
            lines.append(f"  {title[:80]}")

    return "\n".join(lines)
