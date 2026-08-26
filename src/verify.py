"""Pre-flight check: recompute subtotal / tax / total from the line items
using the exact same rule the accounting API uses (tax per code, on the
subtotal for that code, rounded down) and compare against what was
extracted from the invoice.

Why this exists, given the API already does this check on POST:
  - It lets us tell the difference between "our extraction is wrong" and
    "the invoice itself doesn't add up" *before* spending a round trip,
    and gives a human a specific number to check instead of a raw API
    error.
  - It is also the one check explicitly required by the assignment ("a way
    to verify what was read -- at least one check"). Recomputing the math
    is the highest-value check available: it is objective (no judgment
    call), it is exactly what the downstream system will enforce, and it
    catches both extraction errors and (as it turned out with one of the
    12 sample invoices) genuine arithmetic errors on the source document.
"""
from __future__ import annotations

import math

TAX_RATES = {"T10": 0.10, "T08": 0.08}


def verify_amounts(payload: dict) -> dict:
    """Returns {"ok": bool, "issues": [...], "expected": {...}}."""
    issues = []
    lines = payload.get("lines") or []

    amounts = [line.get("amount") for line in lines]
    if any(a is None for a in amounts):
        return {
            "ok": False,
            "issues": ["one or more line amounts are missing; cannot verify"],
            "expected": None,
        }

    expected_subtotal = sum(amounts)
    if payload.get("subtotal") != expected_subtotal:
        issues.append(
            f"subtotal mismatch: extracted {payload.get('subtotal')}, "
            f"recomputed from lines {expected_subtotal}"
        )

    subtotal_by_code: dict[str, int] = {}
    for line in lines:
        code = line.get("tax_code")
        subtotal_by_code[code] = subtotal_by_code.get(code, 0) + line["amount"]

    tax_by_code = {}
    for code, sub in subtotal_by_code.items():
        rate = TAX_RATES.get(code)
        if rate is None:
            issues.append(f"unknown tax code on lines: {code}")
            continue
        tax_by_code[code] = math.floor(sub * rate)

    expected_tax = sum(tax_by_code.values())
    if payload.get("tax_amount") != expected_tax:
        issues.append(
            f"tax_amount mismatch: extracted {payload.get('tax_amount')}, "
            f"recomputed {expected_tax} (by code: {tax_by_code})"
        )

    expected_total = expected_subtotal + expected_tax
    if payload.get("total_amount") != expected_total:
        issues.append(
            f"total_amount mismatch: extracted {payload.get('total_amount')}, "
            f"recomputed {expected_total}"
        )

    return {
        "ok": not issues,
        "issues": issues,
        "expected": {
            "subtotal": expected_subtotal,
            "tax_amount": expected_tax,
            "total_amount": expected_total,
            "tax_by_code": tax_by_code,
        },
    }
