import sys
import os
sys.path.append(r'c:\Users\NEW USER\Desktop\jardx\Backend')
from db.database import portfolio_collection

def check_portfolio_dates():
    portfolios = list(portfolio_collection.find({}))
    print(f"Checking {len(portfolios)} portfolios...")
    for p in portfolios:
        print(f"ID: {p.get('_id')}, House: {p.get('house_name')}, Next Date: '{p.get('next_payment_date')}'")

if __name__ == "__main__":
    check_portfolio_dates()
