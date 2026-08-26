"""Match a supplier name as printed on an invoice to a partner_code in the
accounting system's master data (GET /partners).

Why this exists: invoices from the same supplier print the name differently
(full legal form vs. abbreviation vs. all-katakana), and the API only accepts
a partner_code, not a name. We never invent a partner_code — if nothing
matches, the invoice is routed to human review rather than guessed at,
because posting to the wrong supplier account is a worse failure than a
delay.
"""
from __future__ import annotations

import re
import unicodedata

# Corporate-form tokens that are commonly dropped in casual references.
# Stripping them makes "株式会社山田製作所" and "山田製作所" compare equal.
_CORP_FORMS = ["株式会社", "有限会社", "合同会社", "合資会社", "合名会社"]


def _normalize(name: str) -> str:
    if not name:
        return ""
    n = unicodedata.normalize("NFKC", name)
    n = re.sub(r"\s+", "", n)
    for form in _CORP_FORMS:
        n = n.replace(form, "")
    return n


def match_partner(raw_name: str, partners: list[dict]) -> dict:
    """Returns {"partner_code": str|None, "matched_on": str|None, "confidence": str}.

    Matching order (highest confidence first):
      1. Normalized name equals the partner's registered name
      2. Normalized name equals one of the partner's aliases
      3. Normalized name is a substring of / contains the registered name or an alias
    Anything below (3) is treated as no match — better to queue for review
    than to silently post against the wrong supplier.
    """
    target = _normalize(raw_name)
    if not target:
        return {"partner_code": None, "matched_on": None, "confidence": "none"}

    # Pass 1 + 2: exact match against name or alias
    for p in partners:
        candidates = [p["name"], *p.get("aliases", [])]
        for c in candidates:
            if _normalize(c) == target:
                return {
                    "partner_code": p["partner_code"],
                    "matched_on": c,
                    "confidence": "exact",
                }

    # Pass 3: substring match either direction
    for p in partners:
        candidates = [p["name"], *p.get("aliases", [])]
        for c in candidates:
            nc = _normalize(c)
            if nc and (nc in target or target in nc):
                return {
                    "partner_code": p["partner_code"],
                    "matched_on": c,
                    "confidence": "fuzzy",
                }

    return {"partner_code": None, "matched_on": None, "confidence": "none"}
