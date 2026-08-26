"""Turn a raw extraction (what the model read off the page, in whatever
format the page used) into the exact shape accounting_api.py requires.

This is kept separate from extraction on purpose: the LLM's job is to read
the page faithfully, not to know accounting-system quirks like "the API only
accepts YYYY-MM-DD" or "tax is a code, not a percentage". Mixing the two
means every extraction prompt has to re-teach the model the API's rules, and
a rule change means re-testing extraction. Normalization is deterministic
Python, so it's cheap to test and easy to trust.
"""
from __future__ import annotations

import re
from datetime import date

from partner_match import match_partner

# Reiwa era started 2019-05-01. Reiwa year N => Gregorian year (N + 2018).
# (Only Reiwa appears in this sample set, but Heisei is included since
# Japanese business documents routinely mix eras for years yet.)
_ERA_OFFSETS = {
    "令和": 2018,  # year 1 = 2019
    "平成": 1988,  # year 1 = 1989
}

_ERA_DATE_RE = re.compile(r"(令和|平成)(\d{1,2}|元)年\s*(\d{1,2})月\s*(\d{1,2})日")
_JP_DATE_RE = re.compile(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日")
_SLASH_DATE_RE = re.compile(r"(\d{4})[/年](\d{1,2})[/月](\d{1,2})日?")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class NormalizationWarning(Exception):
    """Raised for anything normalize_date/normalize_tax can't resolve
    confidently. Callers catch this and route the invoice to review instead
    of guessing."""


def normalize_date(raw: str) -> str:
    """Convert a date in any of the observed invoice formats to YYYY-MM-DD.

    Handles, in order: already-ISO, Japanese era (令和/平成), Japanese
    calendar (YYYY年M月D日), and slash format (YYYY/MM/DD).
    """
    if not raw:
        raise NormalizationWarning("empty date")
    raw = raw.strip()

    if _ISO_DATE_RE.match(raw):
        date.fromisoformat(raw)  # validates it's a real calendar date
        return raw

    m = _ERA_DATE_RE.search(raw)
    if m:
        era, year_token, month, day = m.groups()
        year_num = 1 if year_token == "元" else int(year_token)
        year = _ERA_OFFSETS[era] + year_num
        return f"{year:04d}-{int(month):02d}-{int(day):02d}"

    m = _JP_DATE_RE.search(raw)
    if m:
        year, month, day = m.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

    m = _SLASH_DATE_RE.search(raw)
    if m:
        year, month, day = m.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

    raise NormalizationWarning(f"unrecognized date format: {raw!r}")


def tax_pct_to_code(pct) -> str:
    """Map a printed tax rate to the API's tax_code. Defaults to T10 (the
    standard rate) when a line has no explicit rate printed, since every
    sample invoice with a single blended rate uses 10% and states it once
    at the invoice level. This default is logged as an assumption per line
    so a reviewer can see where it was applied."""
    if pct is None:
        return "T10"
    pct = round(float(pct))
    if pct == 10:
        return "T10"
    if pct == 8:
        return "T08"
    raise NormalizationWarning(f"unrecognized tax rate: {pct}%")


def normalize_extraction(raw: dict, partners: list[dict]) -> dict:
    """raw: the extraction JSON (see extract.py's schema).
    Returns {"payload": <API-ready dict or None>, "warnings": [...],
             "partner_match": {...}}.
    Never raises — problems are collected as warnings so the pipeline can
    decide whether to register or route to review.
    """
    warnings: list[str] = []
    payload: dict = {"currency": "JPY"}

    payload["invoice_number"] = (raw.get("invoice_number") or "").strip()
    if not payload["invoice_number"]:
        warnings.append("missing invoice_number")

    for field, raw_field in (("issue_date", "issue_date_raw"), ("due_date", "due_date_raw")):
        try:
            payload[field] = normalize_date(raw.get(raw_field, ""))
        except NormalizationWarning as e:
            warnings.append(f"{field}: {e}")
            payload[field] = None

    partner_match = match_partner(raw.get("supplier_name_raw", ""), partners)
    payload["partner_code"] = partner_match["partner_code"]
    if not partner_match["partner_code"]:
        warnings.append(
            f"no partner match for supplier {raw.get('supplier_name_raw')!r}"
        )
    elif partner_match["confidence"] == "fuzzy":
        warnings.append(
            f"partner matched by fuzzy substring ({raw.get('supplier_name_raw')!r} "
            f"-> {partner_match['matched_on']!r}); worth a human glance"
        )

    lines_out = []
    for i, line in enumerate(raw.get("lines", [])):
        try:
            tax_code = tax_pct_to_code(line.get("tax_rate_pct"))
        except NormalizationWarning as e:
            warnings.append(f"line[{i}]: {e}")
            tax_code = "T10"
        if line.get("tax_rate_pct") is None:
            warnings.append(
                f"line[{i}] ({line.get('description')!r}): no tax rate printed on the "
                f"line, assumed T10 (standard rate) from invoice-level note"
            )
        if line.get("amount") is None:
            warnings.append(f"line[{i}]: missing amount (required by the API)")
        lines_out.append(
            {
                "description": line.get("description") or "(no description read)",
                "quantity": line.get("quantity"),
                "unit": line.get("unit") or "式",
                "unit_price": line.get("unit_price"),
                "amount": line.get("amount"),
                "tax_code": tax_code,
            }
        )
    payload["lines"] = lines_out

    for field in ("subtotal", "tax_amount", "total_amount"):
        val = raw.get(field)
        if val is None:
            warnings.append(f"missing {field}")
        payload[field] = val

    return {"payload": payload, "warnings": warnings, "partner_match": partner_match}
