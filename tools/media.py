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
                await rate_limit("default")
                body = {
                    "requests": [
                        {
                            "image": {"source": {"imageUri": image_url}},
                            "features": [
                                {"type": "WEB_DETECTION", "maxResults": 10},
                                {"type": "LABEL_DETECTION", "maxResults": 5},
                            ],
                        }
                    ]
                }
                data = await post(
                    "https://vision.googleapis.com/v1/images:annotate",
                    params={"key": config.GOOGLE_VISION_KEY},
                    post_json=body,
                )
                resp = data.get("responses", [{}])[0]
                web = resp.get("webDetection", {})
                labels = resp.get("labelAnnotations", [])
                lines.append("── Google Vision ──")
                if labels:
                    lines.append(
                        "Detected content: "
                        + ", ".join(l.get("description", "") for l in labels)
                    )
                entities = web.get("webEntities", [])
                if entities:
                    lines.append("Web entities:")
                    for e in entities:
                        lines.append(
                            f"  {e.get('description', 'N/A')} (score: {e.get('score', 0):.2f})"
                        )
                pages = web.get("pagesWithMatchingImages", [])
                if pages:
                    lines.append(f"\nFound on {len(pages)} pages:")
                    for p in pages[:20]:
                        lines.append(
                            f"  {p.get('url', 'N/A')}  [{p.get('pageTitle', '')[:60]}]"
                        )
                exact = web.get("fullMatchingImages", [])
                if exact:
                    lines.append(f"\nExact matches ({len(exact)}):")
                    for e in exact[:10]:
                        lines.append(f"  {e.get('url', 'N/A')}")
            except Exception as e:
                lines.append(f"Google Vision error: {e}")

        # ── TinEye API ────────────────────────────────────────────────────────
        if config.TINEYE_KEY:
            has_api_result = True
            try:
                await rate_limit("default")
                data = await post(
                    "https://api.tineye.com/rest/search/",
                    params={"api_key": config.TINEYE_KEY, "url": image_url},
                )
                count = data.get("stats", {}).get("total_results", 0)
                matches = data.get("matches", [])
                lines.append(f"\n── TinEye: {count} results ──")
                for m in matches[:6]:
                    lines.append(
                        f"  Domain:  {m.get('domain', 'N/A')}\n"
                        f"  URL:     {m.get('image_url', 'N/A')}\n"
                        f"  Seen:    {m.get('crawl_date', 'N/A')}\n"
                    )
            except Exception as e:
                lines.append(f"\nTinEye error: {e}")

        # ── SauceNAO ──────────────────────────────────────────────────────────
        if config.SAUCENAO_KEY:
            has_api_result = True
            try:
                await rate_limit("default")
                data = await get(
                    "https://saucenao.com/search.php",
                    params={
                        "db": 999,
                        "output_type": 2,
                        "numres": 5,
                        "api_key": config.SAUCENAO_KEY,
                        "url": image_url,
                    },
                )
                results = data.get("results", [])
                lines.append(f"\n── SauceNAO: {len(results)} results ──")
                for r in results:
                    h = r.get("header", {})
                    d = r.get("data", {})
                    ext_urls = d.get("ext_urls", [])
                    lines.append(
                        f"  Similarity: {h.get('similarity')}%  "
                        f"Index: {h.get('index_name', 'N/A')}\n"
                        f"  URL: {ext_urls[0] if ext_urls else 'N/A'}"
                    )
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
            await rate_limit("default")
            payload = {
                "url": image_url,
                "language": language,
                "isOverlayRequired": is_overlay_required,
                "apikey": api_key,
                "OCREngine": 2,
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
