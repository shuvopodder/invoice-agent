# Submission

- Name: Shuvo Podder
- Submission date (YYYY-MM-DD): 2026-08-26
- Hours actually spent: 9H (Approxmately: Friday~Tuesday 1 hour*5days + Today(3~5H) = 9H)
- Repository / how to run it: `./run.sh` in project bash command. Check readme.md for more debug/run based on your os.

## 1. Understanding the request

> The client's email states the problem as data-entry speed: staff retype invoices by hand, month-end turns into overtime, and "AI can read invoices these days." Read literally, the ask is OCR-and-autofill.

> But the email also contains a second, more specific problem, almost in passing: "last month a typo nearly caused us to pay the same invoice twice." That is not a speed problem. It's a lack of a check between "someone entered this" and "money will move." Speeding up entry without adding a check makes that failure mode worse, not better — a human doing data entry by hand is forced to look at every field; an AI system that just autofills faster removes exactly the friction that would have caught the near-double-payment in the first place.

> So the problem I set out to solve is not "read invoices with AI" but "read invoices with AI, and be conservative about which ones get registered without a human looking at them." Concretely: build a pipeline that (a) extracts structured data from whatever format an invoice arrives in, (b) independently verifies that data against itself before trusting it (the recompute-from-lines check in verify.py), (c) registers only what passes verification and matches an unambiguous supplier, and (d) makes everything else visible with a specific, actionable reason — not a black box that either works or silently doesn't. The 12 sample invoices turned out to contain exactly this scenario built in (invoice_01.pdf and invoice_07.jpg are the same physical invoice), which is a strong signal this reading of the request was the intended one.

## 2. What you would have asked the client

| What you wanted to ask | The assumption you made | Why |
|---|---|---|
| If a supplier isn't in the accounting system's partner master yet, should we auto-create it or block? | Block and route to review; never create a partner record automatically. | Registering against the wrong or a freshly-invented supplier code is a worse failure than a delayed invoice — this is exactly the class of mistake the client is trying to eliminate, not introduce a new version of. |
| What confidence bar justifies auto-registering without a human? | Two checks must both pass: the recomputed subtotal/tax/total match the extraction exactly, and the supplier is an exact or high-confidence alias match. Anything else goes to review. | The assignment explicitly asks where I drew this line; I chose the objective, testable bar (does the math agree) over a subjective "the model seemed confident" bar, because the latter is exactly the kind of thing that fails silently. |
| Which LLM/cloud vendor is acceptable for sending supplier invoice data (data residency, vendor contracts, PII concerns)? | Assumed no hard constraint for this exercise and used the Anthropic API, but built extraction as a swappable module (`extract.py`) rather than hard-wiring the choice into the rest of the pipeline. | A real client would have a real answer here (existing vendor relationship, data-residency requirement); guessing wrong would mean re-architecting, so I isolated the decision instead of guessing wrong once. |
| What should happen with an invoice whose own printed numbers don't add up (a supplier-side arithmetic error, not an extraction error)? | Never "fix" it silently by trusting one field over another. Extract what's printed, let verification report the specific mismatch, and send it to review with the actual numbers so a human can call the supplier or accept the discrepancy. | This exact case exists in the sample set (`invoice_09.pdf`, off by ¥1) — see section 5. Auto-correcting would be inventing data; the API will reject a mismatched submission anyway, so silence isn't even an available option, only *how clearly* the reason surfaces. |


## 3. Scoping decisions 

**What you built**

- Extraction into a fixed JSON schema, covering all three document types in
  the sample set (text-layer PDF, scanned image, image-only PDF)
- Extracted invoice's json stored in cached folder e.g: /data/extracted/**.
- Normalization: Japanese-era date conversion (Reiwa → Gregorian), tax
  percentage → tax code, and supplier-name → `partner_code` matching via
  exact/alias/fuzzy match against `GET /partners` (never invented)
- Verification: recomputes subtotal/tax-per-code/total from line items
  using the exact rule `accounting_api.py` uses, before ever calling the API
- Registration against the real API, with graceful handling of
  `DUPLICATE_INVOICE`, `PARTNER_NOT_FOUND`, and `AMOUNT_MISMATCH` —
  including an in-batch dedupe pass so the same physical invoice
  (`invoice_01.pdf` / `invoice_07.jpg`) can't double-post even before
  the API is asked
- A review queue: any invoice that isn't safe to auto-register is written
  to `data/review_queue/<file>.json` with the *specific* reason(s), not a
  generic failure
- A full run log (`data/results.json`) — extraction, normalization,
  verification, and outcome for every invoice, every run
- `run.sh` — the required single command to start everything
- Two extraction modes (live LLM vs. cached fixtures) so the pipeline is
  fully testable without a live API key in a test
  environment — see section 4

**What you left out, and why**

- **Robust review UI.** I just build a simple ui for review doc shortly. UI could be more robust with better UX.

- **Confidence-based routing using the model's own stated confidence.**
  `extraction_confidence` is captured in the schema and stored, but the
  pipeline currently gates only on my own deterministic checks (math
  agrees, partner resolves), not on the model's self-reported confidence.
  Combining both would catch cases where the math happens to agree by
  coincidence on a genuinely bad read.
- **Concurrency / retries / backoff.** The pipeline processes invoices
  sequentially and does not retry a failed LLM or API call. Fine for 12
  invoices; not fine at production volume — see section 7.
- **Persistent storage / real audit log.** `data/results.json` is
  overwritten each run. Adequate for a demo, not for "how would you find
  out if something was registered incorrectly six weeks from now" — see
  section 7 and section 8, #3.
- **Automated tests.** I validated correctness by running the real
  pipeline against the real mock API and inspecting `data/results.json`
  and the review queue by hand for all 12 invoices (see section 6), rather
  than writing a test suite. Given more time, the verification and
  normalization logic (both pure functions, no I/O) are the highest-value
  targets for unit tests.
- **General N-page PDF handling.** `invoice_02.pdf`'s two pages were
  handled as one extraction call over the whole document; I did not build
  logic for invoices that might split line items unpredictably across an
  arbitrary number of pages beyond what a single vision-model context
  window can hold.

- **Handle Exceptions.** i believe i could miss few exception to handle which need to handle in production safety and excellent user experiences. Real software debug & environment improve that fix.


## 4. Design and technology choices

<img width="1693" height="929" alt="21e51662-366a-4619-b109-e71e93ee2815" src="https://github.com/user-attachments/assets/fbe3d8a7-5492-454a-9e1d-19fd1607bdf4" />

```
invoices/*.{pdf,jpg} → extract.py → normalize.py → verify.py → register.py (accounting_client.py)
                                                         │
                                                    fails checks
                                                         ▼
                                          data/review_queue/*.json (reason attached)
```

**Language/runtime:** Python 3, stdlib only for everything except the LLM
call itself (`anthropic`, optional — see below). The assignment's own
mock API is distributed as a zero-dependency single file; I matched that
constraint for the rest of the pipeline so the whole thing runs anywhere
Python 3.9+ exists, no virtualenv wrangling required for a reviewer.
Considered TypeScript — no strong reason to prefer it here, and Python's
stdlib `http.client`/`urllib` covers the one HTTP integration needed
without a dependency.

**Extraction — Anthropic Claude (vision), not a separate OCR engine:**
Chose a multimodal LLM call over classic OCR (e.g. Tesseract) + a
separate parsing/NLP step, for three reasons specific to this data: (1)
the assignment explicitly notes current models handle Japanese invoices
well, and the sample set includes handwriting, stamps, and corrections
that traditional OCR handles poorly without a language model interpreting
context; (2) a single call that returns structured JSON directly skips a
whole separate "turn raw OCR text into fields" step, which is itself
usually another LLM call in practice; (3) it generalizes across layout
differences between suppliers for free, where a classic OCR+regex pipeline
would need per-supplier templates. The tradeoff, acknowledged in section
7, is per-call cost and latency versus a cheaper but more brittle
classic-OCR pipeline — at this volume (12, or even 1,000/month) that
tradeoff clearly favors the LLM approach.

**Why extraction and normalization are separate modules, not one prompt:**
Era-date conversion and tax-code mapping are pure, testable functions with
exactly one correct answer. Asking the model to both read the page *and*
know the accounting system's date/tax-code conventions means every schema
tweak on the accounting side requires re-validating extraction quality.
Keeping the model's job to "transcribe faithfully" and giving deterministic
code the "apply business rules" job means the two evolve independently,
and the risky, business-critical part (does this equal that) is fully
covered by ordinary code review, not a prompt.

**Two extraction modes (live vs. fixture), and why:** Live mode use anthropic api to extract invoices and fetech strctured json then store cached and do furture process. Rather
than submit an untested pipeline or fabricate results, `extract.py`
automatically falls back to pre-extracted JSON fixtures in
`data/extracted/` (produced by directly reading all 12 sample invoices —
the same task being delegated to the model) when `ANTHROPIC_API_KEY` isn't
set. This let every other stage — normalization, verification,
partner-matching, registration, duplicate handling, the review queue — be
built and genuinely exercised end to end against the real mock API (see
section 6 for actual run output), rather than described but untested. The
moment a real API key is present, the exact same pipeline calls the real
model instead; nothing downstream of extraction changes.

**What I decided against:** a database instead of `data/results.json` (out
of scope for 12 invoices — flagged as needed at production scale, section
7/8); a queueing system for the LLM calls (unnecessary at this volume);
a review web UI (section 3); auto-creating unknown suppliers (section 2)
— all cut for the same reason: they add real value at production volume,
not at 12 sample invoices in an 8-hour budget.


## 5. How you used AI, and how you checked it

**What you delegated to AI:** In production, invoice reading itself is
fully delegated to a vision LLM (`extract.py`) — that is the core of what
was asked for. Within building this submission, I also used AI assistance
for code scaffolding (the stdlib HTTP client, argument parsing, file
iteration) and for drafting the extraction prompt's instructions.

**How you verified the output:** Three separate checks, each targeting a
different failure mode:

1. **Never trust the model's stated total — recompute it.** `verify.py`
   independently recomputes subtotal, tax (per code, floored, exactly the
   API's own rule), and total from the line items, and compares against
   what was extracted. A model can misread one line item while still
   confidently transcribing a plausible-looking total; recomputing from
   the parts is the only way to catch that a total agrees with itself but
   not with its own line items.
2. **Never let the model pick a `partner_code` — match deterministically.**
   The model transcribes the supplier name as printed; `partner_match.py`
   does the actual code lookup against `GET /partners`, in plain Python,
   with three tiers (exact name, exact alias, fuzzy substring) and an
   explicit "no match" outcome rather than a best-effort guess. A supplier
   name is exactly the kind of field a model could plausibly hallucinate a
   near-miss for; that risk shouldn't be allowed to touch which ledger
   account money gets posted to.
3. **Never let the model convert dates itself — convert deterministically.**
   Same reasoning: `normalize_date()` is a pure function with unit-testable
   inputs/outputs (era offsets, calendar formats), so an off-by-one on a
   Reiwa-year boundary is a code bug you can catch by testing, not a
   silent model error you'd only notice in production.

**A case where the AI got it wrong (or would have, if trusted naively):**
`invoice_09.pdf` (OSK-26-0128, 大阪機械工業株式会社): the printed total
is ¥147,497. Recomputing from the printed line items
(37×¥2,733 + 37×¥891 = ¥134,088 subtotal, +10% tax floored = ¥13,408) gives
¥147,496 — one yen off from the printed total. I re-checked the read
digits twice; this is the source document's own arithmetic, not a misread.
If the pipeline had simply forwarded the printed "合計" field as
`total_amount` (the naive approach — "trust what's printed"), it either
gets rejected by the API's own `AMOUNT_MISMATCH` check anyway, *or*, if a
future version of this pipeline ever computed line items slightly
differently, could silently register a total that doesn't match its own
lines. `verify.py` catches this before the API call, reports the exact
¥1 discrepancy, and routes it to review instead of either silently
"fixing" it or failing with an opaque error.

## 6. Integrating with the accounting system

Handling summary: verify locally before ever calling the API (catches
`AMOUNT_MISMATCH` and unresolved suppliers before a round trip);
in-batch dedupe *and* rely on the API's own `DUPLICATE_INVOICE` as the
authoritative check (so re-running the pipeline is always safe — see the
duplicate-run test in `README.md`); anything the API itself rejects also
lands in the review queue with the API's own error attached, so a human
sees the same reason the system would show a developer.

Actual result of running `./run.sh` once over all 12 sample invoices
(fixture mode, mock API reset first):

| Invoice | Result | How it was handled |
|---|---|---|
| invoice_01.pdf | Registered (`ACC-0001`) | clean extraction, single 10% rate |
| invoice_02.pdf | Registered (`ACC-0002`) | 2-page PDF, 26 line items, was failed with first attempt but with some coding changes fix that issue, all recomputed correctly |
| invoice_03.pdf | Registered (`ACC-0003`) | mixed 8%/10% tax, split per line, tax-by-code recomputation matched |
| invoice_04.jpg | Registered (`ACC-0004`) | handwritten "received" stamp present but irrelevant to registered fields; ignored |
| invoice_05.jpg | Registered (`ACC-0005`) | clean |
| invoice_06.jpg | Registered (`ACC-0006`) | supplier printed as abbreviation "ヤマダ製作所"; matched to `P-1001` via the aliases list |
| invoice_07.jpg | **Skipped — in-batch duplicate** | same `partner_code` + `invoice_number` as invoice_01.pdf (photograph of the same physical invoice); caught before an API call was even made |
| invoice_08.jpg | Registered (`ACC-0007`) | mixed tax rates; hand-corrected bank account digit noted (not an accounting-API field, flagged for a human paying the invoice, not blocking registration) |
| invoice_09.pdf | **Sent to review** | recomputed total (¥147,496) disagrees with printed total (¥147,497) by ¥1 — see section 5 |
| invoice_10.jpg | **Sent to review** | supplier "新星ロジスティクス株式会社" does not appear in `GET /partners` under any alias — `PARTNER_NOT_FOUND` |
| invoice_11.jpg | Registered (`ACC-0008`) | dates printed in Reiwa era (令和8年...); converted to `2026-02-05` / `2026-03-31` |
| invoice_12.jpg | Registered (`ACC-0009`) | negative discount line (△30,000 → `-30000`) accepted; recomputed subtotal matched |

Result: **9 registered, 2 routed to review with a specific reason each, 1
correctly skipped as a duplicate of an already-registered invoice.**
Re-running the same command a second time (without deleting the API's
data) returns the 9 as `duplicate_per_api` rather than double-posting —
confirmed by an explicit test, see `README.md`.


## 7. Cost, limits, and risk in production

- **Cost per invoice:** one vision-LLM call per invoice (image or PDF page
  + a fixed instruction prompt as input, structured JSON as output).
  Rough order of magnitude: ~1,500–2,500 input tokens (image + prompt),
  ~300–500 output tokens. At current Claude Sonnet-class pricing that's on
  the order of **$0.01–0.02 per single-page invoice**; a multi-page
  invoice like `invoice_02.pdf` (26 lines across 2 pages) costs roughly
  proportionally more. Everything after extraction (normalize, verify,
  register) is local computation and effectively free.
  *** note: cost may vary based on invoices type, size and complexity.
- **Monthly cost at 1,000 invoices/month:** roughly **$15–25/month** in
  LLM cost at that per-invoice estimate, plus margin for retries on
  failed/low-confidence extractions (call it $25–35/month all in). This is
  negligible next to the accounting-overtime cost the client described —
  cost is not the constraint at this volume.
- **Processing time per invoice:** dominated by the LLM call latency,
  roughly 3–8 seconds per invoice; normalize/verify are near-instant;
  registration is a single local HTTP round trip (sub-second against this
  mock API, likely under a second against a real one too). Run
  sequentially, 1,000 invoices/month is well under an hour of wall-clock
  time; trivially parallelizable if that ever mattered.
- **Where this breaks first:** *not* extraction cost or latency. In order:
  (1) **partner-master coverage** — any supplier not yet in `GET
  /partners` blocks automatically, by design (`invoice_10.jpg` in this
  run); at real volume, keeping that master current becomes the actual
  bottleneck, not the AI; (2) **burst load on the LLM API** — invoices
  cluster around month-end, and the current client has no
  concurrency/backoff/rate-limit handling, so a burst could hit provider
  rate limits with no retry logic; (3) **schema assumptions** — the
  pipeline assumes one supplier, one currency (JPY), and a single
  subtotal/tax/total per document; an invoice that doesn't fit that shape
  (e.g. a bundled multi-invoice PDF) would need the schema revisited;
  (4) **the review queue has no escalation path** — invoices land in
  `data/review_queue/` but nothing currently notifies a human or ages them
  out if ignored.
- **How you would find out if something was registered incorrectly:**
  today, by cross-referencing `data/results.json` (every registered
  invoice's full extraction → normalization → verification chain, keyed
  back to the source file) against `GET /invoices`. In production this
  needs to be a real, append-only audit log rather than a file overwritten
  each run — see section 8, #3 — plus a periodic reconciliation job that
  re-sums registered totals against source documents on a schedule, not
  only on request.
  
## 8. What you would do with another 8 hours

1. **Improve human review screen.** Improve UI with more validation and UX support.

2. **Confidence-based routing using the model's own signal.** The schema
   already captures `extraction_confidence` and free-text `notes` from the
   model, but the pipeline doesn't act on them yet — only on my own
   post-hoc numeric checks. Wiring the model's self-reported confidence
   into the review-vs-register decision (and, for low-confidence or
   high-value invoices, running a second independent extraction pass to
   cross-check) would catch cases where the numbers happen to agree by
   coincidence despite a bad read.
3. **Concurrency, retries, and a real audit log.** Sequential processing
   and an overwritten `results.json` are fine for 12 invoices and wrong
   for production. This item turns the demo into something that survives
   a month-end burst and actually answers "was anything registered
   incorrectly" six weeks later rather than only "right after the last
   run." Ordered last because it's pure engineering hardening — valuable,
   but the first two items change what the system can actually handle,
   not just how robustly it handles what it already can.
