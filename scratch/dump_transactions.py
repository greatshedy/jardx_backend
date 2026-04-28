import sys
import os
from dotenv import load_dotenv

# Adjust paths manually
sys.path.append(r'c:\Users\NEW USER\Desktop\jardx\Backend')

from db.database import transactions_collection, user_collection

def check_transactions():
    print("--- Listing all transactions ---")
    transactions = list(transactions_collection.find({}))
    print(f"Total transactions in DB: {len(transactions)}")
    
    for tx in transactions:
        print(f"ID: {tx.get('_id')}, Ref: {tx.get('tx_ref')}, UserID: {tx.get('user_id')}, Status: {tx.get('status')}, Amount: {tx.get('amount')}")

    print("\n--- Listing all users (just IDs) ---")
    users = list(user_collection.find({}))
    for u in users:
        print(f"User ID: {u.get('_id')}, Email: {u.get('email')}")

if __name__ == "__main__":
    check_transactions()
