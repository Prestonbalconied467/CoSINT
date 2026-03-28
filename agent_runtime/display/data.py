"""
agent_runtime/display/data.py

Static display data: tool descriptions (TOOL_INFO) and phase-label patterns
(PHASE_PATTERNS). Separated from display.py so adding/renaming tools only
requires editing this file, not scrolling through rendering code.
"""

from __future__ import annotations

import re

# ── Tool descriptions ──────────────────────────────────────────────────────────
# Maps tool name -> human-readable description shown in live scan output.
# Tools not listed here fall back to an auto-generated label.

TOOL_INFO: dict[str, str] = {
    # Email
    "osint_email_validate": "Validating email address",
    "osint_email_breach_check": "Checking breach databases",
    "osint_email_reputation": "Assessing email reputation",
    "osint_email_social_accounts": "Discovering linked social accounts",
    "osint_google_account_scan": "Scanning Google account data",
    "osint_email_header_analyze": "Analyzing email headers",
    # Domain
    "osint_domain_whois": "WHOIS registration lookup",
    "osint_domain_dns_records": "Fetching DNS records",
    "osint_domain_subdomains": "Enumerating subdomains",
    "osint_domain_certificates": "Searching SSL/TLS certificates (crt.sh)",
    "osint_domain_wayback": "Checking Wayback Machine history",
    "osint_domain_ip_history": "Retrieving IP change history",
    "osint_domain_tech_fingerprint": "Fingerprinting web technologies",
    # Network
    "osint_network_ip_geolocation": "Geolocating IP address",
    "osint_network_asn_lookup": "ASN and organization lookup",
    "osint_network_reputation": "IP reputation and abuse check",
    "osint_network_vpn_proxy_check": "Detecting VPN / proxy / Tor",
    "osint_network_reverse_dns": "Reverse DNS lookup",
    "osint_network_open_ports": "Scanning open ports (Shodan)",
    # Username
    "osint_username_search": "Cross-platform username search",
    # Social
    "osint_social_extract": "Social profile data extraction",
    # Person
    "osint_person_fullname_lookup": "Full name public records lookup",
    "osint_person_darknet_check": "Darknet mention check",
    "osint_person_address_lookup": "Address and location lookup",
    # Company
    "osint_company_registry_lookup": "Company registry lookup",
    "osint_company_financials": "Financial filings lookup",
    "osint_company_employees": "Employee footprint analysis",
    "osint_company_jobs": "Job postings analysis",
    # Phone
    "osint_phone_lookup": "Phone number lookup",
    # Crypto
    "osint_crypto_wallet_btc": "Bitcoin wallet analysis",
    "osint_crypto_wallet_eth": "Ethereum wallet analysis",
    "osint_crypto_wallet_multi": "Cross-chain wallet analysis",
    "osint_crypto_nft_lookup": "NFT holdings lookup",
    # Media
    "osint_media_exif_extract": "Extracting EXIF metadata",
    "osint_media_reverse_image_search": "Reverse image search",
    # Geo
    "osint_geo_forward": "Forward geocoding",
    "osint_geo_reverse": "Reverse geocoding",
    # Leaks
    "osint_leak_paste_search": "Searching paste sites",
    "osint_leak_github_secrets": "GitHub secrets scan",
    # Public records
    "osint_public_news_search": "News and media search",
    "osint_public_court_records": "Public court records search",
    "osint_public_academic_search": "Academic database search",
    "osint_public_bundestag_search": "Parliamentary records search",
    # Scraper
    "osint_scraper_extract": "Scraping website content",
    "osint_scraper_fetch": "Fetching page content",
    # Search
    "osint_web_search": "Web search (Playwright)",
    "osint_web_dork": "Google dork search",
    # Worklog (internal — shown briefly in verbose mode)
    "osint_notes_add": "Saving investigation note",
    "osint_notes_list": "Listing investigation notes",
    "osint_notes_delete": "Deleting investigation note",
    "osint_notes_clear": "Clearing investigation notes",
    "osint_todo_add": "Adding investigation task",
    "osint_todo_update": "Updating investigation task",
    "osint_todo_list": "Listing investigation tasks",
    "osint_todo_summary": "Summarising task progress",
    "osint_todo_clear": "Clearing investigation tasks",
}

# ── Phase label patterns ───────────────────────────────────────────────────────
# Ordered — first match wins.

PHASE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"osint_(email_validate|domain_whois|phone_lookup|person_fullname|username_search|crypto_wallet|network_ip_geo|company_registry)"
        ),
        "Initial Enumeration",
    ),
    (
        re.compile(r"osint_(email_breach|leak_paste|leak_github|person_darknet)"),
        "Breach & Leak Analysis",
    ),
    (
        re.compile(
            r"osint_(domain_dns|domain_sub|domain_cert|domain_wayback|domain_ip_hist|domain_tech|network_asn|network_reputation|network_vpn|network_reverse|network_open)"
        ),
        "Infrastructure Mapping",
    ),
    (
        re.compile(
            r"osint_(username_|social_|email_social|email_reputation|email_header|person_address)"
        ),
        "Social & Identity",
    ),
    (re.compile(r"osint_(geo_)"), "Geolocation"),
    (re.compile(r"osint_(media_)"), "Media Analysis"),
    (
        re.compile(r"osint_(company_|public_court|public_academic|public_bundestag)"),
        "Company Intelligence",
    ),
    (re.compile(r"osint_(crypto_)"), "Blockchain & Crypto"),
    (
        re.compile(
            r"osint_(public_news|public_court|public_academic|public_bundestag)"
        ),
        "Public Records",
    ),
    (re.compile(r"osint_(scrape_|fetch_page)"), "Web Scraping"),
    (re.compile(r"osint_(web_search|web_dork)"), "Web Search"),
    (re.compile(r"osint_(todo_|notes_)"), "Investigation Worklog"),
]


def get_phase_label(tool_name: str) -> str:
    """Return the phase label for a tool name, or 'Investigation' as fallback."""
    for pattern, label in PHASE_PATTERNS:
        if pattern.search(tool_name):
            return label
    return "Investigation"


__all__ = ["TOOL_INFO", "PHASE_PATTERNS", "get_phase_label"]
