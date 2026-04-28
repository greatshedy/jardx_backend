import sys
import os
sys.path.append(r'c:\Users\NEW USER\Desktop\jardx\Backend')
from db.database import user_collection

def check_user_balance(uid):
    u = user_collection.find_one({"_id": uid})
    if u:
        print(f"User ID: {uid}")
        print(f"  Wallet Balance: {u.get('wallet_balance')} (Type: {type(u.get('wallet_balance'))})")
    else:
        print(f"User {uid} not found")

if __name__ == "__main__":
    check_user_balance("352af721-4c8a-42cd-aaf7-214c8a82cd73")
