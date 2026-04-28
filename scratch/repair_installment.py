import sys
import os
import datetime
from dateutil.relativedelta import relativedelta

sys.path.append(r'c:\Users\NEW USER\Desktop\jardx\Backend')
from db.database import portfolio_collection, user_collection, transactions_collection

def repair_failed_installment(portfolio_id, user_id):
    """
    Manually completes a failed installment where money was deducted but records weren't updated.
    """
    print(f"Starting repair for Portfolio: {portfolio_id}")
    
    portfolio = portfolio_collection.find_one({"_id": portfolio_id, "user_id": user_id})
    if not portfolio:
        print("Error: Portfolio not found.")
        return

    monthly_due = portfolio["monthly_payment"]
    months_paid = portfolio["months_paid"] + 1
    remaining_balance = portfolio["remaining_balance"] - monthly_due
    amount_paid = portfolio["amount_paid"] + monthly_due
    
    status = "Completed" if months_paid >= portfolio["duration_months"] or remaining_balance <= 0.5 else "Active"
    
    # Calculate next date
    try:
        current_next_date = datetime.datetime.fromisoformat(portfolio["next_payment_date"])
        next_date = (current_next_date + relativedelta(months=1)).isoformat()
    except Exception:
        next_date = (datetime.datetime.utcnow() + relativedelta(months=1)).isoformat()

    print(f"Updating Portfolio: months_paid {portfolio['months_paid']} -> {months_paid}")
    
    # 1. Update Portfolio
    portfolio_collection.update_one(
        {"_id": portfolio_id},
        {"$set": {
            "months_paid": months_paid,
            "remaining_balance": remaining_balance,
            "amount_paid": amount_paid,
            "status": status,
            "next_payment_date": next_date
        }}
    )
    
    # 2. Add Transaction Record (so it shows in history)
    transactions_collection.insert_one({
        "tx_ref": f"REPAIR-PAY-{portfolio_id[:6].upper()}",
        "user_id": user_id,
        "amount": monthly_due,
        "gateway": "Wallet",
        "type": "DEBIT",
        "purpose": f"Installment Repair: {portfolio['house_name']}",
        "status": "SUCCESS",
        "created_at": datetime.datetime.utcnow().isoformat()
    })
    
    print("Repair Complete! The user's portfolio now reflects the payment.")

if __name__ == "__main__":
    # Specific IDs from the user's report
    USER_ID = "352af721-4c8a-42cd-aaf7-214c8a82cd73"
    PORTFOLIO_ID = "899b5a00-ab77-427e-9b5a-00ab77827eab"
    
    repair_failed_installment(PORTFOLIO_ID, USER_ID)
