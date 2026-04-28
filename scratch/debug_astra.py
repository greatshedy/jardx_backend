import sys
import os
sys.path.append(r'c:\Users\NEW USER\Desktop\jardx\Backend')

from db.database import transactions_collection

def test_astra_find():
    count = transactions_collection.count_documents({})
    print(f"Total count: {count}")
    
    print("\nAttempting find with skip/limit as args...")
    try:
        cursor = transactions_collection.find({}, limit=2, skip=0)
        items = list(cursor)
        print(f"Items found (args): {len(items)}")
    except Exception as e:
        print(f"Failed (args): {e}")

    print("\nAttempting find with options dict...")
    try:
        cursor = transactions_collection.find({}, options={"limit": 2, "skip": 0})
        items = list(cursor)
        print(f"Items found (options): {len(items)}")
    except Exception as e:
        print(f"Failed (options): {e}")

if __name__ == "__main__":
    test_astra_find()
