#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "== Setting up Python virtual environment =="
# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

# Activate virtual environment and install Anthropic
source venv/bin/activate
pip install --upgrade pip
pip install anthropic

echo "== Starting mock accounting API on :8080 =="
python3 accounting_api.py > /tmp/accounting_api.log 2>&1 &
API_PID=$!
trap 'kill $API_PID 2>/dev/null || true' EXIT

# Wait for API to become healthy
for i in $(seq 1 20); do
    if curl -s -o /dev/null http://localhost:8080/health; then
        break
    fi
    sleep 0.25
done

echo "== Running invoice pipeline over invoices/ =="
python3 src/pipeline.py invoices/

echo
echo "== Registered invoices (from the accounting system) =="
curl -s http://localhost:8080/invoices -H "X-API-Key: demo-key-1234" | python3 -m json.tool

echo
echo "API log: /tmp/accounting_api.log"
echo "Review queue: data/review_queue/"
echo "Full run results: data/results.json"
# echo
# echo "(API server will stop when this script exits. Run python3 accounting_api.py"
# echo "directly if you want to keep it up and poke it with curl.)"

# for UI- Review List
REVIEW_PID=""
if [ -n "$(ls -A data/review_queue/*.json 2>/dev/null)" ]; then
  echo
  echo "== Starting the review UI on :8090 (some invoices need a human look) =="
  python3 src/review_server.py > /tmp/review_ui.log 2>&1 &
  REVIEW_PID=$!
  trap 'kill $API_PID $REVIEW_PID 2>/dev/null || true' EXIT
  echo "   Open http://localhost:8090 to review and resolve them."
  echo "   Press Ctrl+C to stop both servers when you're done."
  wait $REVIEW_PID
else
  echo
  echo "(Nothing needs review this run — review UI not started.)"
  echo "(API server will stop when this script exits. Run 'python3 accounting_api.py' "
  echo " directly if you want to keep it up and poke it with curl.)"
fi







# set -euo pipefail
# cd "$(dirname "$0")"

# echo "== Starting mock accounting API on :8080 =="
# python3 accounting_api.py > /tmp/accounting_api.log 2>&1 &
# API_PID=$!
# trap 'kill $API_PID 2>/dev/null || true' EXIT

# for i in $(seq 1 20); do
#   if curl -s -o /dev/null http://localhost:8080/health; then
#     break
#   fi
#   sleep 0.25
# done

# echo "== Running invoice pipeline over invoices/ =="
# python3 src/pipeline.py invoices/

# echo
# echo "== Registered invoices (from the accounting system) =="
# curl -s http://localhost:8080/invoices -H 'X-API-Key: demo-key-1234' | python3 -m json.tool

# echo
# echo "API log: /tmp/accounting_api.log"
# echo "Review queue: data/review_queue/"
# echo "Full run results: data/results.json"
# echo
# echo "(API server will stop when this script exits. Run 'python3 accounting_api.py' "
# echo " directly if you want to keep it up and poke it with curl.)"
