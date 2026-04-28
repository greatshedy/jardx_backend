import sys
import os
import datetime
from dateutil.relativedelta import relativedelta

# Mocking parts of the logic for testing parsing and ordering
def test_parsing_logic(next_payment_date):
    print(f"Testing with date: '{next_payment_date}'")
    try:
        # Robust date parsing
        try:
            # fromisoformat is much more robust
            current_next_date = datetime.datetime.fromisoformat(next_payment_date)
            new_next_date = (current_next_date + relativedelta(months=1)).isoformat()
            print(f"  Success! New date: {new_next_date}")
        except (ValueError, TypeError):
            # Fallback if date is missing or malformed
            new_next_date = (datetime.datetime.utcnow() + relativedelta(months=1)).isoformat()
            print(f"  Fallback triggered! New date: {new_next_date}")
    except Exception as e:
        print(f"  FAILED with unexpected error: {e}")

if __name__ == "__main__":
    print("--- Test 1: Standard ISO with MS ---")
    test_parsing_logic("2026-05-14T16:13:25.990677")
    
    print("\n--- Test 2: Standard ISO without MS ---")
    test_parsing_logic("2026-05-14T16:13:25")
    
    print("\n--- Test 3: Empty String (The Bug) ---")
    test_parsing_logic("")
    
    print("\n--- Test 4: Malformed String ---")
    test_parsing_logic("not-a-date")
