import sys
import os
sys.path.append(r'c:\Users\NEW USER\Desktop\jardx\Backend')
from db.database import portfolio_collection

def check_portfolio_types(pid):
    p = portfolio_collection.find_one({"_id": pid})
    if p:
        print(f"Portfolio ID: {pid}")
        for key, val in p.items():
            print(f"  {key}: {val} (Type: {type(val)})")
    else:
        print(f"Portfolio {pid} not found")

if __name__ == "__main__":
    check_portfolio_types("899b5a00-ab77-427e-9b5a-00ab77827eab")
