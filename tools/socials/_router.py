"""
tools/social/_router.py  –  URL → platform handler dispatcher.

Router entry-point:
    result, platform = await route(url)
    # (str, str)   → dedicated handler matched and succeeded
    # (None, None) → no match, timeout, or handler error → caller falls back to socid_extractor

Error handling
──────────────
Every handler call goes through _dispatch(), which enforces a per-platform timeout and
catches all exceptions. On timeout or error it returns (None, None) so the caller falls
through to socid_extractor silently.

Two dispatch strategies are used depending on how the handler is implemented:

  _dispatch()           — asyncio.wait_for around a plain async coroutine. Correct for
                          all httpx-based handlers. On timeout the coroutine is cancelled
                          and the underlying HTTP connection is closed by httpx's own
                          context manager, so nothing lingers.

  _dispatch_subprocess()— Used exclusively for Instagram. instaloader runs synchronously
                          via asyncio.to_thread, and Python threads cannot be forcibly
                          killed — asyncio.wait_for cancels the Task but the thread keeps
                          running, meaning instaloader's sleep/retry loops would continue
                          in the background for up to 30 minutes.
                          The subprocess strategy runs the Instagram handler in a separate
                          Python process via multiprocessing. On timeout (or any error) the
                          process is explicitly terminated and joined before returning, so
                          no background work survives the call.

Per-platform timeouts (seconds) are defined in _TIMEOUTS. The default is 25 s, which is
slightly above the 15 s httpx timeout used inside most handlers.
Instagram gets 12 s — short enough to abort before instaloader starts its first sleep.
"""

import asyncio
import logging
import multiprocessing
from urllib.parse import urlparse, parse_qs

from ._helpers import _slug
from .code_hosting import github, gitlab, bitbucket
from .social_networks import instagram, twitter, tiktok, bluesky, vk, tumblr, reddit
from .developer import (
    stackoverflow,
    hackernews,
    lobsters,
    devto,
    pypi,
    npm,
    orcid,
    keybase,
    pastebin,
)
from .gaming import steam, steam_by_id, chess, lichess
from .media import (
    youtube,
    youtube_by_channel_id,
    twitch,
    spotify,
    lastfm,
    soundcloud,
    flickr,
    medium,
)
from .misc import linktree, dockerhub, gravatar, duolingo

log = logging.getLogger(__name__)

# ── Per-platform timeouts (seconds) ───────────────────────────────────────
#
# DEFAULT   25 s  — covers one httpx call (15 s) + parsing overhead
# SCRAPER   20 s  — HTML scrapers: httpx fetch is the bottleneck, parse is fast
# SLOW      40 s  — platforms that make several sequential API calls (YouTube,
#                   Twitch, Flickr, Spotify) and may hit secondary endpoints
# INSTAGRAM 12 s  — instaloader will sleep and retry on rate-limit; cut it short
#                   so the fallback to socid_extractor happens quickly

_DEFAULT = 25
_SCRAPER = 20
_SLOW = 40

_TIMEOUTS: dict[str, int] = {
    # Social Networks
    "Instagram": _SLOW,
    "Twitter/X": _DEFAULT,
    "TikTok": _SCRAPER,
    "Bluesky": _DEFAULT,
    "Reddit": _DEFAULT,
    "VK": _DEFAULT,
    "Tumblr": _DEFAULT,
    # Code Hosting
    "GitHub": _SLOW,  # 3 sequential API calls (profile + repos + events)
    "GitLab": _SLOW,
    "Bitbucket": _SLOW,
    # Developer Communities
    "Stack Overflow": _SLOW,
    "HackerNews": _DEFAULT,
    "Lobste.rs": _DEFAULT,
    "Dev.to": _DEFAULT,
    "PyPI": _SLOW,  # XMLRPC call + N package JSON calls
    "npm": _DEFAULT,
    "ORCID": _DEFAULT,
    "Keybase": _DEFAULT,
    "Pastebin": _SCRAPER,
    # Gaming
    "Steam": _DEFAULT,
    "Chess.com": _DEFAULT,
    "Lichess": _DEFAULT,
    # Media & Entertainment
    "YouTube": _SLOW,  # up to 4 API calls (handle → search → channel → uploads)
    "Twitch": _SLOW,  # token + user + channel + followers + clips
    "Spotify": _SLOW,  # token + profile + playlists
    "Last.fm": _SLOW,  # 3 parallel requests
    "SoundCloud": _DEFAULT,
    "Flickr": _SLOW,  # resolve nsid + getInfo + groups + albums
    "Medium": _SCRAPER,
    # Misc
    "Linktree": _SCRAPER,
    "Docker Hub": _DEFAULT,
    "Gravatar": _DEFAULT,
    "Duolingo": _DEFAULT,
}


# ── Dispatch helpers ───────────────────────────────────────────────────────


async def _dispatch(coro, platform: str) -> tuple[str, str] | tuple[None, None]:
    """
    Dispatch for all async (httpx-based) handlers.

    Wraps the coroutine in asyncio.wait_for. On timeout, asyncio cancels the
    coroutine and httpx's context manager closes the connection — nothing lingers.
    Any exception also falls through to socid_extractor.
    """
    timeout = _TIMEOUTS.get(platform, _DEFAULT)
    try:
        result = await asyncio.wait_for(coro, timeout=timeout)
        return result, platform
    except asyncio.TimeoutError:
        log.warning(
            "osint_social_extract: %s timed out after %ds — falling back to socid_extractor",
            platform,
            timeout,
        )
        return None, None
    except Exception as exc:
        log.warning(
            "osint_social_extract: %s raised %s: %s — falling back to socid_extractor",
            platform,
            type(exc).__name__,
            exc,
        )
        return None, None


def _instagram_worker(username: str, result_queue: multiprocessing.Queue) -> None:
    """
    Runs the synchronous instagram() coroutine inside a child process.
    Result (or exception string) is pushed onto result_queue so the parent
    can retrieve it without blocking.
    """
    import asyncio as _asyncio

    try:
        result = _asyncio.run(instagram(username))
        result_queue.put(("ok", result))
    except Exception as exc:
        result_queue.put(("err", str(exc)))


async def _dispatch_subprocess(
    username: str, platform: str
) -> tuple[str, str] | tuple[None, None]:
    """
    Dispatch for Instagram specifically.

    Runs the handler in a separate process so it can be hard-killed on timeout.
    asyncio.to_thread (used inside instagram()) cannot be cancelled once the thread
    is started — the thread keeps running even after asyncio.wait_for fires.
    A subprocess has no such limitation: terminate() + join() guarantee it is dead.

    Flow:
      1. Spawn a child process that runs _instagram_worker.
      2. Poll result_queue in a short asyncio.sleep loop (non-blocking).
      3. On timeout or error: terminate() the process, join() it (with a 3 s hard
         deadline via another asyncio.wait_for), then return (None, None).
    """
    timeout = _TIMEOUTS.get(platform, _SLOW)
    result_queue: multiprocessing.Queue = multiprocessing.Queue()
    proc = multiprocessing.Process(
        target=_instagram_worker,
        args=(username, result_queue),
        daemon=True,  # also dies if the parent process itself exits unexpectedly
    )
    proc.start()

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    result = None

    try:
        while loop.time() < deadline:
            if not result_queue.empty():
                status, payload = result_queue.get_nowait()
                if status == "ok":
                    result = payload
                else:
                    log.warning(
                        "osint_social_extract: %s worker error: %s — falling back to socid_extractor",
                        platform,
                        payload,
                    )
                break
            if not proc.is_alive():
                # Worker exited before pushing a result (e.g. killed by OOM)
                log.warning(
                    "osint_social_extract: %s worker exited unexpectedly — falling back to socid_extractor",
                    platform,
                )
                break
            await asyncio.sleep(0.1)
        else:
            log.warning(
                "osint_social_extract: %s timed out after %ds — terminating worker",
                platform,
                timeout,
            )
    except Exception as exc:
        log.warning(
            "osint_social_extract: %s raised %s: %s — falling back to socid_extractor",
            platform,
            type(exc).__name__,
            exc,
        )
    finally:
        # Always clean up — terminate() is a no-op if the process already exited
        if proc.is_alive():
            proc.terminate()
        try:
            await asyncio.wait_for(
                asyncio.to_thread(proc.join),
                timeout=3,
            )
        except asyncio.TimeoutError:
            # Last resort: SIGKILL
            proc.kill()
            proc.join()

    if result is not None:
        return result, platform
    return None, None


# ── Router ─────────────────────────────────────────────────────────────────


async def route(url: str) -> tuple[str, str] | tuple[None, None]:
    """
    Dispatch a URL to a dedicated platform handler.
    Returns (result_str, platform_name) on match, or (None, None) to fall back
    to socid_extractor.
    """
    if not url.startswith("http"):
        url = f"https://{url}"

    parsed = urlparse(url)
    host = parsed.netloc.lower().replace("www.", "")
    path = parsed.path
    qs = parse_qs(parsed.query)

    async def _with_fallback(awaitable, platform):
        """
        Centralized fallback logic for all handler calls.
        If the handler result is None, empty, or an error string, return (None, None).
        Edit this function for debugging or to change fallback behavior globally.
        """
        result = await awaitable
        # Consider None, empty, or error string as failure
        if not result or not result[0]:
            # For debugging, you can add a print or log here
            # print(f"Fallback triggered for {platform} with result: {result}")
            return None, None
        # Optionally, treat certain error messages as failure
        if isinstance(result[0], str) and (
            "error" in result[0].lower() or "access denied" in result[0].lower()
        ):
            # print(f"Fallback triggered for {platform} due to error message: {result[0]}")
            return None, None
        return result

    # ── Social Networks ────────────────────────────────────────────────────
    if "instagram.com" in host:
        u = _slug(path)
        if u and u not in ("p", "reel", "reels", "stories", "explore", "tv"):
            return await _with_fallback(
                _dispatch_subprocess(u, "Instagram"), "Instagram"
            )

    elif host in ("twitter.com", "x.com"):
        u = _slug(path)
        if u and u not in ("i", "home", "search", "settings", "messages"):
            return await _with_fallback(_dispatch(twitter(u), "Twitter/X"), "Twitter/X")

    elif "tiktok.com" in host:
        u = _slug(path)
        if u:
            return await _with_fallback(_dispatch(tiktok(u), "TikTok"), "TikTok")

    elif "bsky.app" in host:
        parts = [p for p in path.strip("/").split("/") if p]
        if len(parts) >= 2 and parts[0] == "profile":
            return await _with_fallback(
                _dispatch(bluesky(parts[1]), "Bluesky"), "Bluesky"
            )

    elif "reddit.com" in host or "old.reddit.com" in host:
        parts = [p for p in path.strip("/").split("/") if p]
        if len(parts) >= 2 and parts[0] in ("u", "user"):
            return await _with_fallback(_dispatch(reddit(parts[1]), "Reddit"), "Reddit")

    elif "vk.com" in host:
        u = _slug(path)
        if u and u not in ("feed", "messages", "settings", "login", "im"):
            return await _with_fallback(_dispatch(vk(u), "VK"), "VK")

    elif "tumblr.com" in host:
        u = host.replace(".tumblr.com", "") if host != "tumblr.com" else _slug(path)
        if u:
            return await _with_fallback(_dispatch(tumblr(u), "Tumblr"), "Tumblr")

    # ── Code Hosting ───────────────────────────────────────────────────────
    elif "github.com" in host:
        u = _slug(path)
        if u and u not in (
            "explore",
            "topics",
            "trending",
            "marketplace",
            "sponsors",
            "login",
            "signup",
            "about",
            "features",
            "pricing",
        ):
            return await _with_fallback(_dispatch(github(u), "GitHub"), "GitHub")

    elif "gitlab.com" in host:
        u = _slug(path)
        if u and u not in ("explore", "help", "users", "groups", "projects"):
            return await _with_fallback(_dispatch(gitlab(u), "GitLab"), "GitLab")

    elif "bitbucket.org" in host:
        u = _slug(path)
        if u and u not in ("account", "dashboard", "product", "blog"):
            return await _with_fallback(
                _dispatch(bitbucket(u), "Bitbucket"), "Bitbucket"
            )

    # ── Developer Communities ──────────────────────────────────────────────
    elif "stackoverflow.com" in host or "stackexchange.com" in host:
        parts = [p for p in path.strip("/").split("/") if p]
        if len(parts) >= 2 and parts[0] == "users":
            site = (
                "stackoverflow" if "stackoverflow.com" in host else host.split(".")[0]
            )
            return await _with_fallback(
                _dispatch(stackoverflow(parts[1], site=site), "Stack Overflow"),
                "Stack Overflow",
            )

    elif "news.ycombinator.com" in host:
        u = qs.get("id", [None])[0] or _slug(path)
        if u:
            return await _with_fallback(
                _dispatch(hackernews(u), "HackerNews"), "HackerNews"
            )

    elif "lobste.rs" in host:
        parts = [p for p in path.strip("/").split("/") if p]
        if len(parts) >= 2 and parts[0] == "u":
            return await _with_fallback(
                _dispatch(lobsters(parts[1]), "Lobste.rs"), "Lobste.rs"
            )

    elif "dev.to" in host:
        u = _slug(path)
        if u:
            return await _with_fallback(_dispatch(devto(u), "Dev.to"), "Dev.to")

    elif "pypi.org" in host:
        parts = [p for p in path.strip("/").split("/") if p]
        if len(parts) >= 2 and parts[0] == "user":
            return await _with_fallback(_dispatch(pypi(parts[1]), "PyPI"), "PyPI")

    elif "npmjs.com" in host:
        u = _slug(path).lstrip("~")
        if u and u not in ("package", "org", "settings", "support"):
            return await _with_fallback(_dispatch(npm(u), "npm"), "npm")

    elif "orcid.org" in host:
        u = _slug(path)
        if u:
            return await _with_fallback(_dispatch(orcid(u), "ORCID"), "ORCID")

    elif "keybase.io" in host:
        u = _slug(path)
        if u:
            return await _with_fallback(_dispatch(keybase(u), "Keybase"), "Keybase")

    elif "pastebin.com" in host:
        parts = [p for p in path.strip("/").split("/") if p]
        if len(parts) >= 2 and parts[0] == "u":
            return await _with_fallback(
                _dispatch(pastebin(parts[1]), "Pastebin"), "Pastebin"
            )

    # ── Gaming ─────────────────────────────────────────────────────────────
    elif "steamcommunity.com" in host:
        parts = [p for p in path.strip("/").split("/") if p]
        if len(parts) >= 2:
            if parts[0] == "id":
                return await _with_fallback(
                    _dispatch(steam(parts[1]), "Steam"), "Steam"
                )
            elif parts[0] == "profiles":
                return await _with_fallback(
                    _dispatch(steam_by_id(parts[1]), "Steam"), "Steam"
                )

    elif "chess.com" in host:
        parts = [p for p in path.strip("/").split("/") if p]
        if len(parts) >= 2 and parts[0] == "member":
            return await _with_fallback(
                _dispatch(chess(parts[1]), "Chess.com"), "Chess.com"
            )

    elif "lichess.org" in host:
        u = _slug(path)
        if u and u not in ("training", "puzzle", "learn", "forum", "team"):
            return await _with_fallback(_dispatch(lichess(u), "Lichess"), "Lichess")

    # ── Media & Entertainment ──────────────────────────────────────────────
    elif "youtube.com" in host:
        parts = [p for p in path.strip("/").split("/") if p]
        if parts:
            if parts[0] == "channel" and len(parts) >= 2:
                return await _with_fallback(
                    _dispatch(youtube_by_channel_id(parts[1]), "YouTube"), "YouTube"
                )
            elif parts[0] == "user" and len(parts) >= 2:
                return await _with_fallback(
                    _dispatch(youtube(parts[1]), "YouTube"), "YouTube"
                )
            elif parts[0].startswith("@"):
                return await _with_fallback(
                    _dispatch(youtube(parts[0].lstrip("@")), "YouTube"), "YouTube"
                )
            elif parts[0] == "c" and len(parts) >= 2:
                return await _with_fallback(
                    _dispatch(youtube(parts[1]), "YouTube"), "YouTube"
                )

    elif "twitch.tv" in host:
        u = _slug(path)
        if u and u not in ("directory", "search", "subscriptions", "wallet"):
            return await _with_fallback(_dispatch(twitch(u), "Twitch"), "Twitch")

    elif "open.spotify.com" in host:
        parts = [p for p in path.strip("/").split("/") if p]
        if len(parts) >= 2 and parts[0] == "user":
            return await _with_fallback(
                _dispatch(spotify(parts[1]), "Spotify"), "Spotify"
            )

    elif "last.fm" in host or "lastfm.com" in host:
        parts = [p for p in path.strip("/").split("/") if p]
        if len(parts) >= 2 and parts[0] == "user":
            return await _with_fallback(
                _dispatch(lastfm(parts[1]), "Last.fm"), "Last.fm"
            )

    elif "soundcloud.com" in host:
        u = _slug(path)
        if u and u not in ("search", "charts", "discover", "upload", "you"):
            return await _with_fallback(
                _dispatch(soundcloud(u), "SoundCloud"), "SoundCloud"
            )

    elif "flickr.com" in host:
        parts = [p for p in path.strip("/").split("/") if p]
        if len(parts) >= 2 and parts[0] == "people":
            return await _with_fallback(_dispatch(flickr(parts[1]), "Flickr"), "Flickr")

    elif "medium.com" in host:
        u = _slug(path)
        if u:
            return await _with_fallback(_dispatch(medium(u), "Medium"), "Medium")

    # ── Misc ───────────────────────────────────────────────────────────────
    elif "linktr.ee" in host:
        u = _slug(path)
        if u:
            return await _with_fallback(_dispatch(linktree(u), "Linktree"), "Linktree")

    elif "hub.docker.com" in host:
        parts = [p for p in path.strip("/").split("/") if p]
        if len(parts) >= 2 and parts[0] in ("u", "r"):
            return await _with_fallback(
                _dispatch(dockerhub(parts[1]), "Docker Hub"), "Docker Hub"
            )

    elif "gravatar.com" in host:
        u = _slug(path)
        if u:
            return await _with_fallback(_dispatch(gravatar(u), "Gravatar"), "Gravatar")

    elif "duolingo.com" in host:
        parts = [p for p in path.strip("/").split("/") if p]
        if len(parts) >= 2 and parts[0] == "profile":
            return await _with_fallback(
                _dispatch(duolingo(parts[1]), "Duolingo"), "Duolingo"
            )

    return None, None
