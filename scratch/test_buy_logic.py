import sys
import os
import datetime
from dotenv import load_dotenv

# Adjust paths
sys.path.append(r'c:\Users\NEW USER\Desktop\jardx\Backend')

from db.database import user_collection, house_collection, portfolio_collection, transactions_collection

def test_buy_logic():
    user_id = "352af721-4c8a-42cd-aaf7-214c8a82cd73"
    house_id = "839629de-11d2-4831-9629-de11d278314f"
    amount_to_pay = 15000000.0
    
    print(f"Testing purchase for User: {user_id}, House: {house_id}")
    
    try:
        user = user_collection.find_one({"_id": user_id})
        house = house_collection.find_one({"_id": house_id})
        
        if not user or not house:
            print("User or House not found in DB")
            return

        current_balance = float(user.get("wallet_balance", 0.0))
        print(f"Current balance: {current_balance}")
        
        # Simulate outright purchase logic
        outright_price = float(house["house_pricing_plan"][0]["outrightPrice"])
        print(f"Outright price: {outright_price}")
        
        portfolio_item = {
            "user_id": user_id,
            "house_id": house_id,
            "house_name": house["house_name"],
            "plan_type": "outright",
            "total_price": outright_price,
            "amount_paid": amount_to_pay,
            "remaining_balance": 0.0,
            "monthly_payment": 0.0,
            "duration_months": 0,
            "months_paid": 0,
            "next_payment_date": "",
            "status": "Completed",
            "created_at": datetime.datetime.utcnow().isoformat()
        }
        
        # Test inserting
        print("Inserting into portfolio...")
        p_res = portfolio_collection.insert_one(portfolio_item)
        print(f"Portfolio insert result: {p_res}")
        
        print("Inserting into transactions...")
        tx_item = {
            "tx_ref": "TEST-BUY-123",
            "user_id": user_id,
            "amount": amount_to_pay,
            "gateway": "Wallet",
            "type": "DEBIT",
            "purpose": f"Purchase: {house['house_name']}",
            "status": "SUCCESS",
            "created_at": datetime.datetime.utcnow().isoformat()
        }
        t_res = transactions_collection.insert_one(tx_item)
        print(f"Transaction insert result: {t_res}")
        
    except Exception as e:
        print(f"Logic failed with error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_buy_logic()
