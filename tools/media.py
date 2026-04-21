"""
tools/media.py  –  Media & Image Analysis
Tools: osint_media_reverse_image_search

Thin tool registration layer. All browser-based reverse image search logic
and JS extractors live in tools/media_utils.py.
"""

from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from shared import config
from shared.http_client import get, post
from shared.rate_limiter import rate_limit
from tools.helper.media_utils import browser_reverse_image_search

DEFAULT_RATE_LIMIT_BUCKET = "default"
GOOGLE_WEB_MAX_RESULTS = 10
GOOGLE_LABEL_MAX_RESULTS = 5
GOOGLE_PAGES_LIMIT = 20
GOOGLE_EXACT_LIMIT = 10
TINEYE_MATCHES_LIMIT = 6
SAUCENAO_RESULTS_LIMIT = 5
OCR_ENGINE_V2 = 2


async def _google_vision_lines(image_url: str) -> list[str]:
    await rate_limit(DEFAULT_RATE_LIMIT_BUCKET)
    request_body = {
        "requests": [
            {
                "image": {"source": {"imageUri": image_url}},
                "features": [
                    {"type": "WEB_DETECTION", "maxResults": GOOGLE_WEB_MAX_RESULTS},
                    {
                        "type": "LABEL_DETECTION",
                        "maxResults": GOOGLE_LABEL_MAX_RESULTS,
                    },
                ],
            }
        ]
    }
    data = await post(
        "https://vision.googleapis.com/v1/images:annotate",
        params={"key": config.GOOGLE_VISION_KEY},
        post_json=request_body,
    )
    response = data.get("responses", [{}])[0]
    web_data = response.get("webDetection", {})
    labels = response.get("labelAnnotations", [])

    lines = ["── Google Vision ──"]
    if labels:
        lines.append(
            "Detected content: "
            + ", ".join(label.get("description", "") for label in labels)
        )

    entities = web_data.get("webEntities", [])
    if entities:
        lines.append("Web entities:")
        for entity in entities:
            lines.append(
                f"  {entity.get('description', 'N/A')} (score: {entity.get('score', 0):.2f})"
            )

    pages = web_data.get("pagesWithMatchingImages", [])
    if pages:
        lines.append(f"\nFound on {len(pages)} pages:")
        for page in pages[:GOOGLE_PAGES_LIMIT]:
            lines.append(f"  {page.get('url', 'N/A')}  [{page.get('pageTitle', '')[:60]}]")

    exact_matches = web_data.get("fullMatchingImages", [])
    if exact_matches:
        lines.append(f"\nExact matches ({len(exact_matches)}):")
        for match in exact_matches[:GOOGLE_EXACT_LIMIT]:
            lines.append(f"  {match.get('url', 'N/A')}")
    return lines


async def _tineye_lines(image_url: str) -> list[str]:
    await rate_limit(DEFAULT_RATE_LIMIT_BUCKET)
    data = await post(
        "https://api.tineye.com/rest/search/",
        params={"api_key": config.TINEYE_KEY, "url": image_url},
    )
    result_count = data.get("stats", {}).get("total_results", 0)
    matches = data.get("matches", [])

    lines = [f"\n── TinEye: {result_count} results ──"]
    for match in matches[:TINEYE_MATCHES_LIMIT]:
        lines.append(
            f"  Domain:  {match.get('domain', 'N/A')}\n"
            f"  URL:     {match.get('image_url', 'N/A')}\n"
            f"  Seen:    {match.get('crawl_date', 'N/A')}\n"
        )
    return lines


async def _saucenao_lines(image_url: str) -> list[str]:
    await rate_limit(DEFAULT_RATE_LIMIT_BUCKET)
    data = await get(
        "https://saucenao.com/search.php",
        params={
            "db": 999,
            "output_type": 2,
            "numres": SAUCENAO_RESULTS_LIMIT,
            "api_key": config.SAUCENAO_KEY,
            "url": image_url,
        },
    )
    results = data.get("results", [])
    lines = [f"\n── SauceNAO: {len(results)} results ──"]
    for result in results:
        header = result.get("header", {})
        payload = result.get("data", {})
        external_urls = payload.get("ext_urls", [])
        lines.append(
            f"  Similarity: {header.get('similarity')}%  "
            f"Index: {header.get('index_name', 'N/A')}\n"
            f"  URL: {external_urls[0] if external_urls else 'N/A'}"
        )
    return lines


def register(mcp: FastMCP) -> None:

    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_media_reverse_image_search(
        image_url: Annotated[str, Field(description="Direct image URL (https://...)")],
        interactive: Annotated[
            bool,
            Field(
                description=(
                    "Set True when the user is present and can solve CAPTCHAs manually. "
                    "In interactive mode the browser becomes visible and waits up to 90s "
                    "per engine for the user to solve any CAPTCHA. "
                    "Leave False for autonomous scans where no user is present — "
                    "CAPTCHAs are detected but not waited on."
                )
            ),
        ] = False,
    ) -> str:
        """Reverse image search: find where an image appears online via Google Vision, TinEye, SauceNAO, or browser.

        Returns: list of matching URLs with titles, source domains, and similarity scores.
        Read results as a provenance chain:
          earliest_appearance → likely original upload location and identity
          same image + different usernames → same person across multiple platforms, or image theft
          stock/library site hit → image is not unique to the target; do not use for attribution
          news article hit → may name the photo subject directly
        Interactive mode: pause after results — the operator should confirm which hits are relevant
          before pivoting, since the agent cannot see the images.
        Anomaly: reverse search earliest hit predates the target's account creation = stolen image.
        Sources used depend on available API keys (GOOGLE_VISION_KEY, TINEYE_KEY, SAUCENAO_KEY).
        Browser fallback (Yandex + Google Images) works without keys but may need CAPTCHA solving.
        """
        image_url = image_url.strip()
        lines = [f"Reverse image search:\n{image_url}\n"]
        has_api_result = False

        # ── Google Vision ──────────────────────────────────────────────────────
        if config.GOOGLE_VISION_KEY:
            has_api_result = True
            try:
                lines.extend(await _google_vision_lines(image_url))
            except Exception as e:
                lines.append(f"Google Vision error: {e}")

        # ── TinEye API ────────────────────────────────────────────────────────
        if config.TINEYE_KEY:
            has_api_result = True
            try:
                lines.extend(await _tineye_lines(image_url))
            except Exception as e:
                lines.append(f"\nTinEye error: {e}")

        # ── SauceNAO ──────────────────────────────────────────────────────────
        if config.SAUCENAO_KEY:
            has_api_result = True
            try:
                lines.extend(await _saucenao_lines(image_url))
            except Exception as e:
                lines.append(f"\nSauceNAO error: {e}")

        # ── Browser fallback ──────────────────────────────────────────────────
        if not has_api_result:
            lines.append(
                await browser_reverse_image_search(image_url, interactive=interactive)
            )

        return "\n".join(lines)

    @mcp.tool()
    async def osint_media_ocr_image(
        image_url: Annotated[str, Field(description="Direct image URL (https://...)")],
        language: Annotated[
            str, Field(description="Language code for OCR (e.g. 'eng', 'deu')")
        ] = "eng",
        is_overlay_required: Annotated[
            bool, Field(description="If True, include OCR text overlay info")
        ] = False,
    ) -> str:
        """Extract text from an image using the ocr.space API.
        Returns: Extracted text or error message.
        """
        api_key = config.OCR_SPACE_KEY if hasattr(config, "OCR_SPACE_KEY") else None
        if not api_key:
            return "OCR API key (OCR_SPACE_KEY) not set in config."
        try:
            await rate_limit(DEFAULT_RATE_LIMIT_BUCKET)
            payload = {
                "url": image_url,
                "language": language,
                "isOverlayRequired": is_overlay_required,
                "apikey": api_key,
                "OCREngine": OCR_ENGINE_V2,
            }
            resp = await post(
                "https://api.ocr.space/parse/image",
                headers={"apikey": api_key},
                data=payload,
            )
            parsed = resp.get("ParsedResults", [{}])[0]
            text = parsed.get("ParsedText", "")
            if not text:
                return f"No text found. OCR result: {resp}"
            return text.strip()
        except Exception as e:
            return f"OCR error: {e}"
