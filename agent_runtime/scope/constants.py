"""
agent_runtime/scope/constants.py  –  Scope system constants

All hard-coded sets, reason codes, and configuration for scope evaluation.
Nothing in this file has any logic — it is pure data.

Three scope modes:
  strict - rules only, block anything without explicit identifier match
  guided - rules first, AI fallback for ambiguous cases
  ai - AI is the sole judge after internal worklog allow
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Blockchain explorer domains — never valid investigation pivots
# ---------------------------------------------------------------------------
CRYPTO_EXPLORER_DOMAINS: frozenset[str] = frozenset(
    {
        # Bitcoin / multi-chain explorers
        "blockchain.com",
        "blockchain.info",
        "blockchair.com",
        "blockcypher.com",
        "blockscout.com",
        # Ethereum / EVM explorers
        "etherscan.io",
        "etherscan.com",
        "etherchain.org",
        "blkindex.com",
        "ethplorer.io",
        # Specific chain explorers
        "blockstream.info",  # Bitcoin
        "mempool.space",  # Bitcoin
        "tronscan.org",  # Tron
        "polygonscan.com",  # Polygon
        "bscscan.com",  # Binance Smart Chain
        "ftmscan.com",  # Fantom
        "snowtrace.io",  # Avalanche
        "arbiscan.io",  # Arbitrum
        "optimistic.etherscan.io",  # Optimism
        "explorer.solana.com",  # Solana
        "solscan.io",
    }
)

# ---------------------------------------------------------------------------
# Scope decision reason codes
# ---------------------------------------------------------------------------

# Allow codes
SCOPE_ALLOW_NO_ARGS = "ALLOW_NO_ARGS"
SCOPE_ALLOW_NON_STRING_ARGS = "ALLOW_NON_STRING_ARGS"
SCOPE_ALLOW_IDENTIFIER_MATCH = "ALLOW_IDENTIFIER_MATCH"
SCOPE_ALLOW_INTERNAL_WORKLOG = "ALLOW_INTERNAL_WORKLOG"
SCOPE_ALLOW_AI_APPROVED = "ALLOW_AI_APPROVED"

# Block codes
SCOPE_BLOCK_DOMAIN = "BLOCK_DOMAIN_OUT_OF_SCOPE"
SCOPE_BLOCK_URL_HOST = "BLOCK_URL_HOST_OUT_OF_SCOPE"
SCOPE_BLOCK_DOMAIN_IN_ARG = "BLOCK_DOMAIN_IN_ARG_OUT_OF_SCOPE"
SCOPE_BLOCK_VALUE_IN_ARG = "BLOCK_VALUE_IN_ARG_OUT_OF_SCOPE"
SCOPE_BLOCK_STRICT_UNMATCHED = "BLOCK_STRICT_UNMATCHED"
SCOPE_BLOCK_PARSE_FAILURE = "BLOCK_PARSE_FAILURE"
SCOPE_BLOCK_AI_REJECTED = "BLOCK_AI_REJECTED"
SCOPE_BLOCK_AI_ERROR = "BLOCK_AI_ERROR"

# ---------------------------------------------------------------------------
# Attribution tool prefixes
# ---------------------------------------------------------------------------
# Tool name prefixes whose *output* artifacts are trusted as attribution pivots.
# Only output artifacts (not arg values) from these tools are eligible to be
# promoted into scope.
ATTRIBUTABLE_DOMAIN_TOOL_PREFIXES: tuple[str, ...] = (
    "osint_username_",
    "osint_social_",
    "osint_person_",
    "osint_email_",
    "osint_company_",
    "osint_scrape_",
    "osint_fetch_",
    "osint_web_",
    "osint_public_",
    "osint_crypto_",
    "osint_blockchain_",
    "osint_wallet_",
)

# ---------------------------------------------------------------------------
# AI scope rating thresholds
# ---------------------------------------------------------------------------
# Per-kind thresholds used by guided/ai modes.
# Higher = harder to approve = lower false-positive risk.
# Domain and IP have the widest blast radius so they require the strongest signal.
SCOPE_AI_APPROVAL_THRESHOLDS: dict[str, float] = {
    "domain": 0.70,  # Expands entire scope; one bad domain poisons the investigation
    "ip": 0.70,  # Shared infrastructure risk is high; CDNs/VPNs look plausible
    "username": 0.65,  # Platform names and numeric IDs pass format checks easily
    "phone": 0.62,  # Direct real-world identifier; false positive is a privacy risk
    "email": 0.58,  # Specific enough that context matches are usually real
    "crypto": 0.55,  # Long unique addresses; accidental collisions are near impossible
}
SCOPE_AI_APPROVAL_THRESHOLDS_EXPLORE: dict[str, float] = {
    "domain": 0.50,  # Still the highest risk even in explore
    "ip": 0.50,
    "username": 0.45,
    "phone": 0.42,
    "email": 0.38,
    "crypto": 0.35,
}
# Fallback for unknown / untyped target kinds.
SCOPE_AI_APPROVAL_THRESHOLD_DEFAULT: float = 0.60

# explore mode leans toward allowing plausible threads — lower bar reflects
# that the investigator is following leads, not confirming known identifiers.
SCOPE_AI_APPROVAL_THRESHOLD_EXPLORE: float = 0.40


__all__ = [
    "CRYPTO_EXPLORER_DOMAINS",
    "ATTRIBUTABLE_DOMAIN_TOOL_PREFIXES",
    "SCOPE_AI_APPROVAL_THRESHOLDS",
    "SCOPE_AI_APPROVAL_THRESHOLD_DEFAULT",
    "SCOPE_AI_APPROVAL_THRESHOLD_EXPLORE",
    "SCOPE_ALLOW_NO_ARGS",
    "SCOPE_ALLOW_NON_STRING_ARGS",
    "SCOPE_ALLOW_IDENTIFIER_MATCH",
    "SCOPE_ALLOW_INTERNAL_WORKLOG",
    "SCOPE_ALLOW_AI_APPROVED",
    "SCOPE_BLOCK_DOMAIN",
    "SCOPE_BLOCK_URL_HOST",
    "SCOPE_BLOCK_DOMAIN_IN_ARG",
    "SCOPE_BLOCK_VALUE_IN_ARG",
    "SCOPE_BLOCK_STRICT_UNMATCHED",
    "SCOPE_BLOCK_PARSE_FAILURE",
    "SCOPE_BLOCK_AI_REJECTED",
    "SCOPE_BLOCK_AI_ERROR",
]
