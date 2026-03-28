from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from .models import ArtifactObservation, RelationSummary, ToolEvidenceRecord
from shared.url_utils import extract_domain

MAX_ARTIFACTS_PER_EVIDENCE = 12

# Target type detection regexes
_EMAIL_RE = re.compile(r"^[\w.+\-]+@[\w\-]+\.[\w.]+$")
_IP_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")
_IPV6_RE = re.compile(r"^[0-9a-fA-F:]+:[0-9a-fA-F:]+$")
_PHONE_RE = re.compile(r"^\+?[\d\s\-().]{7,20}$")
_CRYPTO_BTC = re.compile(r"^(bc1|[13])[a-zA-HJ-NP-Z0-9]{25,62}$")
_CRYPTO_ETH = re.compile(r"^0x[0-9a-fA-F]{40}$")
_DOMAIN_RE = re.compile(r"^(https?://)?([a-zA-Z0-9\-]+\.)+[a-zA-Z]{2,}(/.*)?$")
_MEDIA_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".svg",
    ".avif",
)

_INLINE_EMAIL_RE = re.compile(r"\b[\w.+\-]+@[\w\-]+\.[\w.]+\b")
_INLINE_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_INLINE_DOMAIN_RE = re.compile(r"\b(?:[a-zA-Z0-9\-]+\.)+[a-zA-Z]{2,}\b")
_INLINE_PHONE_RE = re.compile(r"\+\d[\d\s()\-]{6,18}\d")
_INLINE_ETH_RE = re.compile(r"\b0x[0-9a-fA-F]{40}\b")
_INLINE_BTC_RE = re.compile(r"\b(?:bc1|[13])[a-zA-HJ-NP-Z0-9]{25,62}\b")
_INLINE_USERNAME_RE = re.compile(r"(?<!\w)@[A-Za-z0-9_][A-Za-z0-9_.-]{2,31}\b")


def _is_media_url(target: str) -> bool:
    if not target.lower().startswith(("http://", "https://")):
        return False
    path = (urlsplit(target).path or "").lower()
    return path.endswith(_MEDIA_EXTENSIONS)


def detect_type(target: str) -> str:
    """Infer target type from its value."""
    t = target.strip()
    if _EMAIL_RE.match(t):
        return "email"
    if _IP_RE.match(t) or _IPV6_RE.match(t):
        return "ip"
    if _CRYPTO_ETH.match(t) or _CRYPTO_BTC.match(t):
        return "crypto"
    if _PHONE_RE.match(t) and (t.startswith("+") or t.replace(" ", "").isdigit()):
        return "phone"
    if t.startswith("@"):
        return "username"
    if _DOMAIN_RE.match(t) and extract_domain(t):
        if _is_media_url(t):
            return "media"
        return "domain"
    if " " in t:
        return "person"
    return "username"


def normalize_target_value(value: str) -> str:
    value = (value or "").strip()
    if value.startswith("@"):
        return value[1:]
    return value


def extract_artifact_observations(
    *, text: str, source: str, username: str = ""
) -> list[ArtifactObservation]:
    observations: list[ArtifactObservation] = []
    seen: set[tuple[str, str]] = set()
    patterns = [
        ("email", _INLINE_EMAIL_RE),
        ("ip", _INLINE_IPV4_RE),
        ("domain", _INLINE_DOMAIN_RE),
        ("phone", _INLINE_PHONE_RE),
        ("crypto", _INLINE_ETH_RE),
        ("crypto", _INLINE_BTC_RE),
        ("username", _INLINE_USERNAME_RE),
    ]
    for kind, pattern in patterns:
        for match in pattern.findall(text or ""):
            value = normalize_target_value(match)
            if kind == "domain":
                value = extract_domain(value)
            key = (kind, value.lower())
            if not value or key in seen:
                continue
            seen.add(key)
            observations.append(
                ArtifactObservation(value=value, kind=kind, source=source)
            )
            if len(observations) >= MAX_ARTIFACTS_PER_EVIDENCE:
                return observations
    # Generalized extraction: find URLs containing the username
    if username:
        url_pat = r"https?://[\w.-]+/[\w@.-]*" + re.escape(username) + r"[\w@.-]*"
        url_matches = re.findall(url_pat, text or "")
        if len(url_matches) == 1:
            match = url_matches[0]
            key = ("profile_url", match.lower())
            if key not in seen:
                seen.add(key)
                observations.append(
                    ArtifactObservation(value=match, kind="profile_url", source=source)
                )
                # Also extract the base domain
                base = match.split(username, 1)[0]
                if base.endswith("/"):
                    base = base[:-1]
                domain = extract_domain(base)
                if domain:
                    dkey = ("domain", domain.lower())
                    if dkey not in seen:
                        seen.add(dkey)
                        observations.append(
                            ArtifactObservation(
                                value=domain, kind="domain", source=source
                            )
                        )
        elif len(url_matches) > 1:
            # Fallback: ambiguous, pass all matches as a single artifact
            joined = ", ".join(url_matches)
            key = ("ambiguous_profile_url", joined.lower())
            if key not in seen:
                seen.add(key)
                observations.append(
                    ArtifactObservation(
                        value=joined, kind="ambiguous_profile_url", source=source
                    )
                )
        # else: no matches, do nothing
    return observations


def infer_target_scope(
    *,
    primary_target: str,
    related_targets: list[str],
    tool_args: dict,
    raw_output: str,
) -> list[str]:
    joined = " ".join(
        [
            json.dumps(tool_args, ensure_ascii=False, sort_keys=True),
            raw_output or "",
        ]
    ).lower()
    scoped: list[str] = []
    for candidate in [primary_target, *related_targets]:
        normalized = normalize_target_value(candidate)
        variants = {candidate.lower(), normalized.lower()}
        if detect_type(candidate) == "username":
            variants.add(f"@{normalized.lower()}")
        if any(v and v in joined for v in variants):
            scoped.append(candidate)
    return scoped


def build_relation_summary(
    *,
    primary_target: str,
    related_targets: list[str],
    correlate_targets: bool,
    evidence: list[ToolEvidenceRecord],
) -> RelationSummary | None:
    if not related_targets:
        return None

    artifact_hits: dict[str, set[str]] = {}
    for record in evidence:
        scoped_targets = record.target_scope or []
        for obs in record.observed_artifacts:
            owners = set(scoped_targets)
            if not owners:
                continue
            artifact_hits.setdefault(obs.value.lower(), set()).update(owners)

    shared = sorted(
        value for value, owners in artifact_hits.items() if len(owners) >= 2
    )
    conflicts: list[str] = []
    return RelationSummary(
        mode="correlate_targets" if correlate_targets else "same_subject_enrichment",
        primary_target=primary_target,
        related_targets=related_targets,
        shared_artifacts=shared[:20],
        conflicting_artifacts=conflicts,
    )


__all__ = [
    "MAX_ARTIFACTS_PER_EVIDENCE",
    "build_relation_summary",
    "detect_type",
    "extract_artifact_observations",
    "infer_target_scope",
    "normalize_target_value",
]
