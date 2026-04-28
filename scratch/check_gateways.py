import sys
import os
from dotenv import load_dotenv

sys.path.append(r'c:\Users\NEW USER\Desktop\jardx\Backend')

from db.database import transactions_collection

def check_gateways():
    txs = list(transactions_collection.find({}))
    gateways = set(tx.get('gateway') for tx in txs)
    print(f"Unique Gateways: {gateways}")
    for tx in txs:
        print(f"ID: {tx.get('_id')}, Ref: {tx.get('tx_ref')}, Gateway: {tx.get('gateway')}, Status: {tx.get('status')}")

if __name__ == "__main__":
    check_gateways()
