import sys
import os
from dotenv import load_dotenv

# Adjust paths
sys.path.append(r'c:\Users\NEW USER\Desktop\jardx\Backend')

from db.database import transactions_collection, portfolio_collection, user_collection

def inspect_finance_data():
    print("--- User Wallet Summary ---")
    users = list(user_collection.find({}))
    total_wallet_balance = sum(u.get('wallet_balance', 0) for u in users)
    print(f"Total Users: {len(users)}")
    print(f"Total Wallet Balance: {total_wallet_balance}")

    print("\n--- Recent Transactions ---")
    transactions = list(transactions_collection.find({}, limit=5))
    for t in transactions:
        print(t)

    print("\n--- Portfolio (Sales) Summary ---")
    portfolios = list(portfolio_collection.find({}))
    total_revenue = sum(p.get('amount_paid', 0) for p in portfolios)
    print(f"Total Properties Sold: {len(portfolios)}")
    print(f"Total Revenue from Properties: {total_revenue}")

if __name__ == "__main__":
    inspect_finance_data()
