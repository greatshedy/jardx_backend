import sys
import os
from dotenv import load_dotenv

# Adjust paths manually
sys.path.append(r'c:\Users\NEW USER\Desktop\jardx\Backend')

from db.database import transactions_collection

def test_sort():
    user_id = "352af721-4c8a-42cd-aaf7-214c8a82cd73"
    print(f"Testing find with sort for user: {user_id}")
    try:
        # Try the fixed implementation style
        find_result = transactions_collection.find({"user_id": user_id})
        print(f"Find result type: {type(find_result)}")
        
        try:
            sorted_result = find_result.sort({"created_at": -1})
            print("Successfully called .sort() with dictionary on cursor")
            transactions = list(sorted_result)
            print(f"Found {len(transactions)} transactions")
        except Exception as e:
            print(f"Error calling .sort() with dictionary: {e}")
            
        # Try alternative: sort as parameter in find
        try:
            print("\nTrying sort as parameter...")
            # Use sort dictionary instead of -1 if using Data API directly
            find_param = transactions_collection.find(
                {"user_id": user_id},
                sort={"created_at": -1}
            )
            transactions = list(find_param)
            print(f"Found {len(transactions)} transactions with parameter sort")
        except Exception as e:
            print(f"Error with parameter sort: {e}")

    except Exception as e:
        print(f"General error: {e}")

if __name__ == "__main__":
    test_sort()
