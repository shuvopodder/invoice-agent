"""Read a single invoice file (PDF or image) into structured JSON.

Two modes, chosen automatically:

  1. LIVE:   if ANTHROPIC_API_KEY is set, call the Anthropic API with the
             page as an image/PDF input and ask for strict JSON back.
  2. FIXTURE: otherwise, load a pre-extracted JSON file from
             data/extracted/<name>.json if one exists.


Fixture mode exists because this pipeline was built and demoed in debug mode where several test api call needed, 
instead using high credit i make this mode for debug and coding needs.
"""
from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
from pathlib import Path

from dotenv_loader import load_dotenv

load_dotenv()  # picks up .env in the project root, if present -- see README.md

logger = logging.getLogger(__name__)

EXTRACTED_DIR = Path(__file__).resolve().parent.parent / "data" / "extracted"
MODEL = os.environ.get("EXTRACTION_MODEL", "claude-sonnet-5")

FIELD_GLOSSARY = """
請求書 / 御請求書 = Invoice        請求書番号 = Invoice number
発行日 = Issue date                お支払期日 = Due date
品名・摘要 = Description            数量 = Quantity
単位 = Unit                        単価 = Unit price
金額 = Amount                      小計 = Subtotal
消費税 = Consumption tax            税率 = Tax rate
合計 / 御請求金額 = Total           登録番号 = Tax registration number
御中 = Addressed to (company)      お振込先 = Bank transfer details
""".strip()

EXTRACTION_SCHEMA_INSTRUCTIONS = f"""
You are reading a Japanese business invoice. Field labels you may encounter:

{FIELD_GLOSSARY}

Read the document and return ONLY a JSON object (no markdown fences, no
commentary) with exactly this shape:

{{
  "supplier_name_raw": "<the issuing company's name exactly as printed>",
  "invoice_number": "<invoice number exactly as printed>",
  "issue_date_raw": "<issue date exactly as printed, do not convert it>",
  "due_date_raw": "<due date exactly as printed, do not convert it>",
  "lines": [
    {{
      "description": "<line description>",
      "quantity": <number or null>,
      "unit": "<unit text, e.g. 個/式/時間, or null>",
      "unit_price": <integer or null>,
      "amount": <integer, required>,
      "tax_rate_pct": <10 or 8, or null if not printed on this line>
    }}
  ],
  "subtotal": <integer, the printed 小計>,
  "tax_amount": <integer, the printed total 消費税>,
  "total_amount": <integer, the printed 合計 / 御請求金額>,
  "extraction_confidence": "<high|medium|low>",
  "notes": "<anything unusual: handwriting, stamps, corrections, illegible fields>"
}}

Rules:
- Transcribe dates and names exactly as printed -- do not normalize eras,
  slashes, or company-name abbreviations yourself. Downstream code does that.
- If a value is struck through or hand-corrected, extract the CORRECTED
  value and say so in "notes"; never silently keep the crossed-out original.
- If multiple tax rates appear on one invoice, set tax_rate_pct per line,
  not once for the whole invoice.
- Ignore stamps, handwritten annotations unrelated to the amounts (e.g.
  "urgent", received-stamps), except to note them.
- If any required field cannot be read with confidence, still return your
  best guess but set "extraction_confidence": "low" and explain why in "notes".
""".strip()


def _fixture_path(file_path: str) -> Path:
    stem = Path(file_path).stem  # e.g. "invoice_01"
    return EXTRACTED_DIR / f"{stem}.json"


def _load_fixture(file_path: str) -> dict:
    fp = _fixture_path(file_path)
    if not fp.exists():
        raise FileNotFoundError(
            f"No fixture at {fp} and ANTHROPIC_API_KEY is not set -- "
            f"nothing to extract from for {file_path}"
        )
    with open(fp, encoding="utf-8") as f:
        return json.load(f)


def _extract_live(file_path: str) -> dict:
    """Call the Anthropic API with the invoice as a document/image input."""
    import anthropic  # local import: only required in LIVE mode

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    mime, _ = mimetypes.guess_type(file_path)
    with open(file_path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("utf-8")

    block_type = "document" if mime == "application/pdf" else "image"
    content = [
        {
            "type": block_type,
            "source": {"type": "base64", "media_type": mime, "data": data},
        },
        {"type": "text", "text": EXTRACTION_SCHEMA_INSTRUCTIONS},
    ]

    resp = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(text)


# def extract_invoice(file_path: str) -> dict:
#     """Returns the raw extraction dict for one invoice file."""
#     if os.environ.get("ANTHROPIC_API_KEY"):
#         logger.info("%s: extracting via live Anthropic API (model=%s)", file_path, MODEL)
#         return _extract_live(file_path)
#     logger.info("%s: no ANTHROPIC_API_KEY set, loading cached fixture", file_path)
#     return _load_fixture(file_path)

def extract_invoice(file_path: str) -> dict:
    """Returns the raw extraction dict for one invoice file."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        logger.info("%s: extracting via live Anthropic API (model=%s)", file_path, MODEL)
        result = _extract_live(file_path)
        
        # Save the result to the extracted directory. e.g: /data/extracted
        try:
            target_path = _fixture_path(file_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)  # Create data/extracted/ if missing
            with open(target_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            logger.info("Saved extracted invoice to %s", target_path)
        except Exception as e:
            logger.error("Failed to save invoice for %s: %s", file_path, e)
            
        return result
        
    logger.info("%s: no ANTHROPIC_API_KEY set, loading cached fixture", file_path)
    return _load_fixture(file_path)