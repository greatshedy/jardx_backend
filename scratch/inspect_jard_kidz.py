import sys
import os
from dotenv import load_dotenv

# Adjust paths
sys.path.append(r'c:\Users\NEW USER\Desktop\jardx\Backend')

from db.database import jard_kidz_collection

def list_jard_kidz():
    print("--- Listing Jard Kidz Plans ---")
    data = list(jard_kidz_collection.find({}))
    if not data:
        print("No jard kidz plans found")
        return

    print(f"Found {len(data)} plans")
    for item in data:
        print(item)

if __name__ == "__main__":
    list_jard_kidz()
