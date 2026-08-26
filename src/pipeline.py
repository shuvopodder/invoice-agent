#!/usr/bin/env python3
"""End-to-end invoice intake pipeline.

    python3 src/pipeline.py invoices/

For each invoice file:
  1. extract    -- read the page into structured JSON (LLM or fixture)
  2. normalize  -- era dates -> ISO, tax % -> tax_code, supplier name -> partner_code
  3. verify     -- recompute subtotal/tax/total from the lines
  4. register   -- POST to the accounting API, UNLESS step 2 or 3 raised a
                   concern -- those go to the review queue instead.

Design principle (see SUBMISSION.md section 1/3 for the full reasoning):
automation handles the invoices where extraction was clean, the numbers add
up, and the supplier is unambiguous. Anything else is queued for a human
with the specific reason attached, not silently forced through -- the
client's stated fear was a near-double-payment, and the cheapest way to
reintroduce that risk is to make an automated system that swallows
uncertainty instead of surfacing it.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv_loader import load_dotenv

load_dotenv()  # picks up .env in the project root, if present -- see README.md

from accounting_client import AccountingClient
from extract import extract_invoice
from logging_setup import setup_logging
from normalize import normalize_extraction
from verify import verify_amounts

logger = logging.getLogger(__name__)

BASE_URL = os.environ.get("ACCOUNTING_API_URL", "http://localhost:8080") # Get Accounting API url from env or take default one
API_KEY = os.environ.get("ACCOUNTING_API_KEY", "demo-key-1234") # Get Accounting API key from env or take default one

ROOT = Path(__file__).resolve().parent.parent
REVIEW_DIR = ROOT / "data" / "review_queue"
RESULTS_PATH = ROOT / "data" / "results.json"

SUPPORTED_SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png"}


def process_one(file_path: Path, client: AccountingClient, partners: list[dict], seen_in_batch: set):
    result = {"file": file_path.name, "status": None, "detail": None}
    logger.info("processing %s", file_path.name)

    # 1. Extract
    try:
        raw = extract_invoice(str(file_path))
    except Exception as e:
        logger.exception("extraction failed for %s", file_path.name)
        result.update(status="review", detail=f"extraction failed: {e}")
        return result
    logger.debug("%s: extracted %s", file_path.name, raw)

    # 2. Normalize
    norm = normalize_extraction(raw, partners)
    payload = norm["payload"]
    result["extracted"] = raw
    result["normalized"] = payload
    result["warnings"] = list(norm["warnings"])
    for w in norm["warnings"]:
        logger.debug("%s: warning: %s", file_path.name, w)

    blocking = [w for w in norm["warnings"] if _is_blocking(w)]

    # 3. Verify (only meaningful once dates/partner/amounts are present)
    verification = None
    if payload.get("lines") and all(l.get("amount") is not None for l in payload["lines"]):
        verification = verify_amounts(payload)
        result["verification"] = verification
        if not verification["ok"]:
            logger.info("%s: verification failed: %s", file_path.name, verification["issues"])
            blocking.extend(verification["issues"])
        else:
            logger.debug("%s: verification passed", file_path.name)

    if not payload.get("partner_code"):
        blocking.append("no partner_code resolved")
    if not payload.get("issue_date") or not payload.get("due_date"):
        blocking.append("date normalization failed")

    # In-batch duplicate guard: catches the "same invoice photographed
    # twice" case (invoice_01.pdf / invoice_07.jpg in the sample set)
    # before it even reaches the API.
    dedupe_key = (payload.get("partner_code"), payload.get("invoice_number"))
    if dedupe_key in seen_in_batch:
        logger.warning("%s: duplicate of an invoice already processed this run: %s", file_path.name, dedupe_key)
        result.update(
            status="skipped_duplicate_in_batch",
            detail=f"same partner+invoice_number already processed this run: {dedupe_key}",
        )
        return result

    if blocking:
        logger.warning("%s: routed to review: %s", file_path.name, blocking)
        result.update(status="review", detail=blocking)
        _write_review_item(result)
        return result

    seen_in_batch.add(dedupe_key)

    # 4. Register
    logger.debug("%s: POST /invoices %s", file_path.name, dedupe_key)
    status, body = client.register(payload)
    if status == 201:
        logger.info("%s: registered as %s", file_path.name, body["data"]["accounting_id"])
        result.update(status="registered", detail=body["data"])
    elif status == 409:
        # Expected outcome, not a failure: the API is the source of truth
        # for "have we already paid this", which is exactly the check that
        # was missing manually and nearly caused a double payment.
        logger.info("%s: already registered (API reports duplicate)", file_path.name)
        result.update(status="duplicate_per_api", detail=body["error"])
    else:
        logger.warning("%s: rejected by API (%s): %s", file_path.name, status, body["error"])
        result.update(status="rejected", detail=body["error"])
        _write_review_item(result)

    return result


def _is_blocking(warning: str) -> bool:
    """Not every warning should stop automation -- a fuzzy-but-confident
    partner match is worth logging but not worth a human's time. Missing
    data, unresolved dates, and unmatched suppliers are."""
    non_blocking_markers = ("fuzzy substring", "assumed T10")
    return not any(m in warning for m in non_blocking_markers)


def _write_review_item(result: dict):
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    out = REVIEW_DIR / f"{Path(result['file']).stem}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)


def main():
    log_path = setup_logging()
    logger.info("pipeline run started")

    if len(sys.argv) != 2:
        print("usage: python3 src/pipeline.py <invoices_dir>", file=sys.stderr)
        sys.exit(1)

    invoices_dir = Path(sys.argv[1])
    files = sorted(
        p for p in invoices_dir.iterdir() if p.suffix.lower() in SUPPORTED_SUFFIXES
    )
    if not files:
        logger.error("no invoice files found in %s", invoices_dir)
        print(f"No invoice files found in {invoices_dir}")
        sys.exit(1)

    client = AccountingClient(BASE_URL, API_KEY)
    status, body = client.health()
    if status != 200:
        logger.error("accounting API not reachable at %s: %s", BASE_URL, body)
        print(f"Accounting API not reachable at {BASE_URL}: {body}", file=sys.stderr)
        sys.exit(1)
    partners = client.partners()
    logger.info("loaded %d partners from accounting API", len(partners))

    seen_in_batch: set = set()
    results = []
    for f in files:
        r = process_one(f, client, partners, seen_in_batch)
        results.append(r)
        print(f"  {f.name:20s} -> {r['status']}")

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)

    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    logger.info("run complete: %s", counts)

    print("\nSummary:")
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}")
    print(f"\nFull results:   {RESULTS_PATH}")
    print(f"Review queue:   {REVIEW_DIR}  ({len(list(REVIEW_DIR.glob('*.json'))) if REVIEW_DIR.exists() else 0} items)")
    print(f"Execution log:  {log_path}")


if __name__ == "__main__":
    main()
