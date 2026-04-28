import sys
import os
sys.path.append(r'c:\Users\NEW USER\Desktop\jardx\Backend')
from db.database import transactions_collection

def check_user_transactions(uid):
    txs = list(transactions_collection.find({"user_id": uid}))
    print(f"User ID: {uid} | Found {len(txs)} transactions")
    for tx in txs:
        print(f"  Ref: {tx.get('tx_ref')} | Amount: {tx.get('amount')} | Type: {tx.get('type')} | Status: {tx.get('status')} | Created: {tx.get('created_at')}")

if __name__ == "__main__":
    check_user_transactions("352af721-4c8a-42cd-aaf7-214c8a82cd73")
