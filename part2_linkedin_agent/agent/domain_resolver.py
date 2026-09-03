"""Step 2: company name (+ LinkedIn slug) -> official company website domain.

Uses Clearbit's public, keyless autocomplete endpoint (no auth, no rate-limit key
needed -- it's the same endpoint that powers countless "type your company name"
signup forms). A bare company name is often ambiguous (e.g. "Harvey" resolves to
Harvey Norman / Harvey Nichols before the actual AI startup), so we also try a
query built from the LinkedIn slug (e.g. "harvey-ai" -> "Harvey ai"), which
disambiguates correctly in practice.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import requests

from .linkedin import HEADERS

AUTOCOMPLETE_URL = "https://autocomplete.clearbit.com/v1/companies/suggest"

NOISE_WORDS = {"inc", "llc", "ltd", "co", "corp", "corporation", "group", "the"}


@dataclass
class DomainResult:
    domain: str | None
    matched_name: str | None
    query_used: str | None
    candidates: list[dict]


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _slug_query(company_name: str, company_slug: str | None) -> str | None:
    """Build a disambiguating query like 'Harvey ai' from slug 'harvey-ai'."""
    if not company_slug:
        return None
    slug_tokens = [t for t in re.split(r"[-_]", company_slug) if t and t not in NOISE_WORDS]
    name_tokens = {t.lower() for t in re.split(r"\s+", company_name) if t}
    extra = [t for t in slug_tokens if t.lower() not in name_tokens]
    if not extra:
        return None
    return f"{company_name} {' '.join(extra)}"


def _query_clearbit(query: str, timeout: int) -> list[dict]:
    resp = requests.get(
        AUTOCOMPLETE_URL, params={"query": query}, headers=HEADERS, timeout=timeout
    )
    if resp.status_code != 200:
        return []
    try:
        return resp.json() or []
    except ValueError:
        return []


def _name_variations(company_name: str) -> list[str]:
    """Generate query variations to try when exact name doesn't match.

    Examples:
    - "Chalk" -> ["Chalk Inc", "Chalk Ltd", ...]
    - "Good AI" -> ["Good AI", "Good AI Inc", ...]
    """
    base = company_name.strip()
    variations = [base]

    # Try common suffixes
    for suffix in ["Inc", "Inc.", "Ltd", "Ltd.", "LLC", "Corp", "Corp."]:
        variations.append(f"{base} {suffix}")

    # Try without the last word if it looks like a descriptor
    tokens = base.split()
    if len(tokens) > 1:
        last = tokens[-1].lower()
        if last in {"ai", "labs", "systems", "tech", "software", "group", "solutions"}:
            variations.append(" ".join(tokens[:-1]))

    return variations


# Country-code TLDs that contradict a US-based job posting. Deliberately a small
# denylist of "this is definitely somewhere else" rather than an attempt to map
# every ccTLD to a country: the only judgement being made is "an exact name match
# on a foreign ccTLD is weaker evidence than a near match on a neutral domain",
# and a short list is enough for that.
_FOREIGN_CCTLDS = (
    ".co.za", ".co.uk", ".co.in", ".com.au", ".com.br", ".co.nz", ".co.jp",
    ".com.tr", ".tr", ".de", ".fr", ".nl", ".es", ".it", ".pl", ".se", ".no", ".dk", ".fi",
    ".in", ".cn", ".jp", ".ru", ".za", ".ie", ".ch", ".at", ".be",
)

_US_HINTS = ("united states", "usa", "u.s.", "us", "america")


def _looks_us(location: str | None) -> bool:
    if not location:
        return False
    loc = location.strip().lower()
    return loc in _US_HINTS or any(h in loc for h in ("united states", "usa", ", us"))


def _better_than(chosen: dict, candidates: list[dict], target_norm: str, location: str | None):
    """Replace `chosen` only when it is itself plainly in the wrong country.

    Company names are not unique across countries, and an exact name match is
    not automatically the right company. Real case: a posting for "MANTECH",
    Machine Learning Engineer, United States. Clearbit returns both
    "MANTECH" -> mantech.co.za (a South African firm, an exact string match) and
    "ManTech International" -> mantech.com (the US contractor actually hiring).
    Name alone confidently picks the wrong one.

    The check is deliberately on `chosen` alone, not on the candidate list. An
    earlier version scanned the whole list for *any* foreign exact match and then
    substituted an alternative - which regressed "Bevel", where Clearbit returns
    five exact matches: it saw bevel.co.jp further down the list and swapped the
    already-correct getbevel.com out for bevel.com.tr. If the candidate being
    returned is fine, nothing here should touch it.
    """
    dom = (chosen.get("domain") or "").lower()
    if not dom.endswith(_FOREIGN_CCTLDS) or not _looks_us(location):
        return None

    for alt in candidates:
        alt_dom = (alt.get("domain") or "").lower()
        alt_norm = _normalize(alt.get("name", ""))
        # The alternative must be on a neutral domain whose own root *is* the
        # company name - so "ManTech International" at mantech.com qualifies,
        # while an unrelated firm that merely starts with the same letters does
        # not. Prefix matching is safe only under that second condition; used
        # alone it is the trap that once matched "Aven" to "Aventon".
        if (
            not alt_dom.endswith(_FOREIGN_CCTLDS)
            and alt_norm.startswith(target_norm)
            and alt_dom.split(".")[0] == target_norm
        ):
            return alt
    return None


def resolve_company_domain(
    company_name: str,
    company_slug: str | None = None,
    timeout: int = 10,
    location: str | None = None,
) -> DomainResult:
    queries: list[str] = []

    # Priority 1: slug-disambiguated query
    disambiguated = _slug_query(company_name, company_slug)
    if disambiguated:
        queries.append(disambiguated)

    # Priority 2: exact company name
    queries.append(company_name)

    # Priority 3: name variations (+ Inc, + Ltd, etc)
    queries.extend(_name_variations(company_name))

    target_norm = _normalize(company_name)
    all_candidates: list[dict] = []

    for query in queries:
        candidates = _query_clearbit(query, timeout)
        all_candidates.extend(candidates)
        if not candidates:
            continue

        # Only accept an exact normalized-name match...
        for c in candidates:
            if _normalize(c.get("name", "")) == target_norm:
                # ...unless that match is itself in a country the job is not in.
                better = _better_than(c, candidates, target_norm, location)
                if better:
                    return DomainResult(better["domain"], better["name"], query, candidates)
                return DomainResult(c["domain"], c["name"], query, candidates)

    return DomainResult(None, None, None, all_candidates)
