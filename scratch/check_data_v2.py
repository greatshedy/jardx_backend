import sys
import os
from dotenv import load_dotenv

# Adjust paths manually
sys.path.append(r'c:\Users\NEW USER\Desktop\jardx\Backend')

from db.database import transactions_collection, house_collection

def check_data():
    print("--- Transactions ---")
    transactions = list(transactions_collection.find({}))
    print(f"Total transactions: {len(transactions)}")
    for tx in transactions:
        print(f"Ref: {tx.get('tx_ref')}, User: {tx.get('user_id')}, Type: {tx.get('type','CREDIT')}, Purpose: {tx.get('purpose')}")

    print("\n--- Houses Example ---")
    houses = list(house_collection.find({}).limit(1))
    for h in houses:
        print(f"House ID: {h.get('_id')}")
        print(f"Pricing Plan: {h.get('house_pricing_plan')}")

if __name__ == "__main__":
    check_data()
