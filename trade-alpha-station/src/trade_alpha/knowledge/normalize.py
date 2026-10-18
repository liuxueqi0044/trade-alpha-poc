"""Conservative normalization and deterministic candidate blocking."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

from .models import EntityKind

_SPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]", flags=re.UNICODE)
_CORPORATE_SUFFIXES = frozenset(
    {
        "inc",
        "incorporated",
        "ltd",
        "limited",
        "llc",
        "plc",
        "corp",
        "corporation",
        "co",
        "company",
        "gmbh",
        "ag",
        "sa",
        "sarl",
        "bv",
        "oy",
        "pte",
        "sdn",
        "bhd",
        "股份有限公司",
        "有限公司",
        "公司",
    }
)


@dataclass(frozen=True)
class NormalizedIdentity:
    original_name: str
    normalized_name: str
    name_tokens: tuple[str, ...]
    country: str | None
    registration_number: str | None
    domain: str | None
    email_domain: str | None
    phone: str | None
    postal_code: str | None
    h3: str | None
    language: str | None
    transliteration: str | None


def _text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().strip()
    return _SPACE.sub(" ", _PUNCT.sub(" ", value)).strip()


def _domain(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip().casefold()
    if "@" in candidate and "://" not in candidate:
        candidate = candidate.rsplit("@", 1)[1]
    parsed = urlparse(candidate if "://" in candidate else f"//{candidate}")
    return parsed.hostname.removeprefix("www.") if parsed.hostname else None


def _phone(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    return digits if len(digits) >= 7 else None


def normalize_identity(
    entity_type: EntityKind, name: str, attributes: Mapping[str, object] | None = None
) -> NormalizedIdentity:
    """Normalize comparison values without losing the source's original value."""
    attrs = attributes or {}
    normalized = _text(name)
    tokens = tuple(token for token in normalized.split() if token)
    if entity_type is EntityKind.ORGANIZATION:
        tokens = tuple(token for token in tokens if token not in _CORPORATE_SUFFIXES)
    registration = attrs.get("registration_number")
    country = attrs.get("country")
    return NormalizedIdentity(
        original_name=name,
        normalized_name=" ".join(tokens),
        name_tokens=tokens,
        country=str(country).upper() if country else None,
        registration_number=re.sub(r"\W", "", str(registration)).upper() if registration else None,
        domain=_domain(str(attrs["domain"])) if attrs.get("domain") else None,
        email_domain=_domain(str(attrs["email"])) if attrs.get("email") else None,
        phone=_phone(str(attrs["phone"])) if attrs.get("phone") else None,
        postal_code=_text(str(attrs["postal_code"])).replace(" ", "")
        if attrs.get("postal_code")
        else None,
        h3=str(attrs["h3"]) if attrs.get("h3") else None,
        language=str(attrs["language"]) if attrs.get("language") else None,
        transliteration=str(attrs["transliteration"]) if attrs.get("transliteration") else None,
    )


def blocking_keys(entity_type: EntityKind, identity: NormalizedIdentity) -> frozenset[str]:
    """Return type-prefixed blocks; no broad global-name-only blocking."""
    prefix = entity_type.value
    keys: set[str] = set()
    if identity.registration_number:
        keys.add(f"{prefix}:registration:{identity.registration_number}")
    for domain in (identity.domain, identity.email_domain):
        if domain:
            keys.add(f"{prefix}:domain:{domain}")
    if identity.phone:
        keys.add(f"{prefix}:phone:{identity.phone}")
    token = identity.name_tokens[0] if identity.name_tokens else None
    if token and identity.country:
        keys.add(f"{prefix}:country-name:{identity.country}:{token}")
    if token and (identity.postal_code or identity.h3):
        keys.add(f"{prefix}:place-name:{identity.postal_code or identity.h3}:{token}")
    return frozenset(keys)
