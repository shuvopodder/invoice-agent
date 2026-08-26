# Invoice intake automation — Sample Trading Co.

Reads Japanese supplier invoices (PDF or scanned image), extracts structured
data, checks it against itself, and registers it with the accounting
system's API — flagging anything it isn't confident about for a human
instead of guessing.

## Run it Quickly
Navigate to the project root directory using your terminal, command prompt, or an integrated terminal in an IDE (like VS Code), and execute the starter script:

```bash
./run.sh
```

### What this script does:
* **Starts the Mock API:** Boots up a local mock accounting API on `http://localhost:8080`.
* **Processes Invoices:** Runs the ingestion pipeline over every document located in the `invoices/` directory.
* **Displays Output:** Prints a processing summary and dumps the full registered-invoice dataset from the API database.
* **Human Review UI:** Start preview on `http://localhost:8090` invoice review queue. Flagged invoices listed here and Accounting staff can review flagged invoices, inspect validation findings, and manually approve resubmission..
* **Automated Cleanup:** Gracefully shuts down the mock API server as soon as the execution loop finishes.

### Prerequisites:
* **Python 3.9+** is required.
* **Zero Dependencies (Demo Mode):** The demo uses cached, pre-extracted JSON responses from `data/extracted/` by default. No `pip install` commands, external heavy libraries, or Anthropic LLM API keys are required for the initial demonstration run.
* **Production Mode Setup:** The `run.sh` script automatically checks and installs necessary libraries like `anthropic` if you switch over to live vision LLM extraction/real ANTHROPIC_API_KEY is set on .env file.


*** Note: if ```./run.sh``` command not work properly or need extra permission please use the troubleshooting fix from end of this documents.


## Run Seperately & Debug

To start the pieces separately (useful for poking at the API with curl
while the pipeline runs):

```bash
python3 accounting_api.py &          # terminal 1
python3 src/pipeline.py invoices/    # terminal 2
```

## What it does

```
invoices/*.{pdf,jpg}
        │
        ▼
  extract.py        — vision LLM reads the page into a fixed JSON schema
        │              (or loads data/extracted/<name>.json if no API key
        │               is configured — see "Two modes" below)
        ▼
  normalize.py       — era dates → ISO 8601, tax % → tax_code, supplier
        │               name → partner_code (fuzzy-matched against
        │               GET /partners, never invented)
        ▼
  verify.py          — recomputes subtotal / tax / total from the line
        │               items using the exact rule the accounting API
        │               uses, and compares against what was extracted
        ▼
   ┌────┴────┐
   ▼         ▼
 clean     any concern (missing data, no partner match, amounts
   │        don't add up, duplicate within this run)
   ▼         ▼
register    data/review_queue/<file>.json — the specific reason is
  to API      attached, not just "failed"
```

Every run also writes `data/results.json`: one record per input file with
what was extracted, what was normalized, what verification found, and what
happened when (or whether) it was registered. That file is the audit trail
for "how do I find out if something was registered incorrectly" (see
`SUBMISSION.md` section 7).

## Two modes: live extraction vs. cached fixtures

To still demonstrate a working,
testable pipeline, `extract.py` works in two modes, chosen automatically:

- **`ANTHROPIC_API_KEY` set** → calls the Anthropic API with each invoice
  page as an image/PDF input and asks for the JSON schema described in
  `src/extract.py`.
- **not set** → loads `data/extracted/<invoice_name>.json`, a pre-extracted
  fixture. These fixtures were produced by earlier live api call, cached for offline use and are
  checked into the repo specifically so `normalize.py` → `verify.py` →
  `register.py` can be exercised end to end without a live key.

Nothing else about the pipeline changes between the two modes — normalize,
verify, and register all operate on the same JSON shape either way. See
`SUBMISSION.md` section 4 for why this tradeoff was made instead of, say,
mocking the LLM call itself.

## Files

| Path | Purpose |
|---|---|
| `run.sh` | single command to start everything |
| `accounting_api.py` | the mock accounting system, copied verbatim from the assignment spec — not modified |
| `src/extract.py` | LLM-based (or fixture-based) extraction |
| `src/normalize.py` | era-date conversion, tax-code mapping, partner matching |
| `src/verify.py` | recomputes amounts and compares against extraction |
| `src/accounting_client.py` | stdlib HTTP client for the accounting API |
| `src/partner_match.py` | supplier-name → partner_code matching |
| `src/pipeline.py` | orchestrates the above end to end |
| `src/review_server.py` | review UI backend |
| `review_ui/index.html` | review UI frontend |
| `data/extracted/*.json` | cached extraction fixtures (12 sample invoices) |
| `data/review_queue/*.json` | invoices the pipeline declined to auto-register, with reasons |
| `data/results.json` | full run log, written fresh each run |
| `invoices/` | the 12 sample invoices |
| `SUBMISSION.md` | the assignment writeup |

## Dependencies

Zero required dependencies to run the demo (`./run.sh` with fixtures).
`anthropic` (pip) is only needed if you export `ANTHROPIC_API_KEY` to run
live extraction instead of fixtures.



## 🛠️ Troubleshooting

### Port Conflict Issues
The application requires ports `8080` and `8090` to be free. If these ports are already in use by other services, you will encounter connection errors. You can resolve this by either freeing up the ports or changing the application configuration.

#### Option 1: Free up the ports

Find and terminate the processes currently occupying the ports.

**Windows (Command Prompt):**
```cmd
:: Find the process ID (PID)
netstat -ano | findstr :8080
netstat -ano | findstr :8090

:: Kill the process (replace <PID> with the actual number found)
taskkill /PID <PID> /F
```

**macOS / Linux (Terminal):**
```bash
# Find and kill the process running on port 8080
kill -9 \$(lsof -t -i:8080)

# Find and kill the process running on port 8090
kill -9 \$(lsof -t -i:8090)
```

---

#### Option 2: Change the application ports

If you prefer to run the scripts on different ports, update the source code configuration:

1. **`accounting_api.py`**: Locate the server startup logic (usually at the bottom of the file) and change `8080` to an open port (e.g., `8081`).
2. **`review_server.py`**: Locate the startup logic and change `8090` to an open port (e.g., `8091`).

*Note: If you change these ports, ensure you also update any API base URLs or environment variables pointing to these services.*



### environment and antropic installation


```bash
python3 -m venv venv

source venv/bin/activate 

pip install anthropic
```


### Mac Command Error Fix/Debug:
If run.sh cannot be executed on macOS, try the following fixes.

1. Add execute permissions: Run chmod +x ./run.sh to allow the shell to run the file.

2. Remove quarantine attribute: Run xattr -d com.apple.quarantine ./run.sh if macOS blocked the downloaded or transferred script.

3. Run via interpreter: Execute bash ./run.sh or sh ./run.sh to bypass the executable flag requirement.

4. Grant Full Disk Access: Go to System Settings > Privacy & Security > Full Disk Access and turn on Terminal if system files are restricted.

#### Quick Fix
For most run.sh execution issues, try:
```bash
chmod +x ./run.sh
xattr -d com.apple.quarantine ./run.sh
./run.sh
```
