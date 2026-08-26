#test_loader.py
import logging
import os
import sys
from pathlib import Path
  
from dotenv_loader import load_dotenv

from extract import extract_invoice #to test live extraction

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

def llmExtract() :
    extracted_json = extract_invoice("/Users/shuvopodder/GitHub/venturas_assessment_task/invoice-agent/invoices/invoice_01.pdf")
    print("\n=== Live Function Execution Results ===")
    print(f"Target Supplier: {extracted_json.get('supplier_name_raw')}")
    print(f"Total Amount   : {extracted_json.get('total_amount')}")
    print(f"Full JSON Summary:\n{extracted_json}")     
    
def test_my_dotenv():
    # Print environment before loading
    print("Before:", os.environ.get("ANTHROPIC_API_KEY"))

    # Run the function
    load_dotenv()

    # Print environment after loading
    print("After:", os.environ.get("ANTHROPIC_API_KEY"))


if __name__ == "__main__":
    # test_my_dotenv()
    llmExtract()



# import logging
# from accounting_client import AccountingClient

# # Enable logging to see the network requests in the terminal
# logging.basicConfig(level=logging.DEBUG)

# def run_checks():
#     # Update these values to match your local development environment
#     MOCK_URL = "http://localhost:8080" 
#     MOCK_KEY = "your_actual_api_key_here"
    
#     print("=== STARTING ACCOUNTING CLIENT CHECKS ===")
#     client = AccountingClient(base_url=MOCK_URL, api_key=MOCK_KEY)
    
#     # 1. Health Check
#     try:
#         status, health = client.health()
#         print(f"[SUCCESS] Health check status: {status} -> {health}")
#     except Exception as e:
#         print(f"[FAILURE] Health check failed: {e}")

#     # 2. Partners Check
#     try:
#         partners = client.partners()
#         print(f"[SUCCESS] Retrieved {len(partners)} partners.")
#     except Exception as e:
#         print(f"[FAILURE] Fetching partners failed: {e}")

#     # 3. Invoices Check
#     try:
#         invoices = client.existing_invoices()
#         print(f"[SUCCESS] Retrieved {len(invoices)} existing invoices.")
#     except Exception as e:
#         print(f"[FAILURE] Fetching invoices failed: {e}")

# if __name__ == "__main__":
#     run_checks()
