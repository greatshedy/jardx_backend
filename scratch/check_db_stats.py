import sys
import os
sys.path.append(r'c:\Users\NEW USER\Desktop\jardx\Backend')

from db.database import portfolio_collection, user_collection, transactions_collection, jard_kidz_collection

def check_db_stats():
    # Portfolios
    portfolios = list(portfolio_collection.find({}))
    print(f"Portfolios count: {len(portfolios)}")
    if portfolios:
        print(f"Sample portfolio amount_paid: {portfolios[0].get('amount_paid')}")
    
    # Users
    users = list(user_collection.find({}))
    print(f"Users count: {len(users)}")
    if users:
        print(f"Sample user wallet_balance: {users[0].get('wallet_balance')}")

    # Transactions
    pending = transactions_collection.count_documents({"status": "PENDING"}, upper_bound=1000)
    print(f"Pending transactions: {pending}")

    # JardKidz
    active_plans = jard_kidz_collection.count_documents({"status": "Active"}, upper_bound=1000)
    print(f"Active JardKidz plans: {active_plans}")

if __name__ == "__main__":
    check_db_stats()
