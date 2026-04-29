from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from fastapi.responses import JSONResponse
from db.database import user_collection, house_collection, portfolio_collection, transactions_collection
from model import PropertyPurchase, PortfolioModel
from utill import get_token, send_purchase_email, reassemble_base64_string, process_referral_logic
import datetime
import secrets
from dateutil.relativedelta import relativedelta
import logging

logger = logging.getLogger("jardx")

router = APIRouter(prefix="/users/portfolio", tags=["Portfolio"])

@router.post("/buy")
def buy_property(purchase: PropertyPurchase, background_tasks: BackgroundTasks, data: dict = Depends(get_token)):
    try:
        user_id = data.get("id")
        if not user_id:
            return JSONResponse(status_code=401, content={"message": "Invalid user session"})
        
        user = user_collection.find_one({"_id": user_id})
        house = house_collection.find_one({"_id": purchase.house_id})

        if not user or not house:
            return JSONResponse(status_code=404, content={"message": "User or House not found"})
        
        # Check if user has enough balance
        current_balance = float(user.get("wallet_balance", 0.0))
        if current_balance < purchase.amount_to_pay:
            return JSONResponse(status_code=400, content={"message": "Insufficient wallet balance. Please top up your wallet."})

        # Determine the correct plan index
        plan_idx = purchase.plan_index if purchase.plan_index is not None else 0
        house_plan = house["house_pricing_plan"][plan_idx]

        # Process Outright vs Installment
        if purchase.plan_type == "outright":
            # outright purchase uses the price
            outright_price = float(house_plan["outrightPrice"])
            
            # create portfolio logic
            portfolio_item = {
                "user_id": user_id,
                "house_id": purchase.house_id,
                "house_name": house["house_name"],
                "plan_type": purchase.plan_type,
                "total_price": outright_price,
                "amount_paid": purchase.amount_to_pay,
                "remaining_balance": 0.0,
                "monthly_payment": 0.0,
                "duration_months": 0,
                "months_paid": 0,
                "next_payment_date": "",
                "status": "Completed",
                "house_image": house.get("house_image", [])[0] if house.get("house_image") else "",
                "created_at": datetime.datetime.utcnow().isoformat()
            }

        
        else:
            # Installment Handling
            # house_plan is already defined above via plan_idx
            if purchase.plan_index is None:
                return JSONResponse(status_code=400, content={"message": "Installment plan index missing."})
                
            num_months = int(house_plan["numInstallments"][purchase.plan_index])
            percentage_increase = float(house_plan["percentageIncrease"])
            outright_price = float(house_plan["outrightPrice"])
            down_payment_perc = float(house_plan["downPayment"])

            total_percentage_increase = (percentage_increase / 100) * (purchase.plan_index + 1)
            total_payment = outright_price + (outright_price * total_percentage_increase)
            
            expected_downpayment = total_payment * (down_payment_perc / 100)
            remaining_balance = total_payment - purchase.amount_to_pay
            monthly_payment = remaining_balance / num_months

            next_payment_date = (datetime.datetime.utcnow() + relativedelta(months=1)).isoformat()

            portfolio_item = {
                "user_id": user_id,
                "house_id": purchase.house_id,
                "house_name": house["house_name"],
                "plan_type": purchase.plan_type,
                "total_price": total_payment,
                "amount_paid": purchase.amount_to_pay,
                "remaining_balance": remaining_balance,
                "monthly_payment": monthly_payment,
                "duration_months": num_months,
                "months_paid": 0,  # 0 months paid because this is just downpayment
                "next_payment_date": next_payment_date,
                "status": "Active",
                "house_image": house.get("house_image", [])[0] if house.get("house_image") else "",
                "created_at": datetime.datetime.utcnow().isoformat()
            }


        # Deduct wallet balance
        new_balance = current_balance - purchase.amount_to_pay
        user_collection.update_one({"_id": user_id}, {"$set": {"wallet_balance": new_balance}})

        # Record Portfolio Items
        portfolio_collection.insert_one(portfolio_item)
        
        # Extract Image for History
        house_image = ""
        if isinstance(house.get("house_image"), list) and len(house["house_image"]) > 0:
            first_img_item = house["house_image"][0]
            house_image = reassemble_base64_string(first_img_item) if isinstance(first_img_item, list) else first_img_item

        # Record Transaction for History (Using URL instead of heavy base64)
        first_house_img = ""
        if house.get("house_image") and len(house["house_image"]) > 0:
            raw_img = house["house_image"][0]
            # If it's a URL (string) we store it; if it's legacy chunked data (list), we skip it
            if isinstance(raw_img, str):
                first_house_img = get_image_url(raw_img)

        transactions_collection.insert_one({
            "tx_ref": f"BUY-{secrets.token_hex(6).upper()}",
            "user_id": user_id,
            "amount": purchase.amount_to_pay,
            "gateway": "Wallet",
            "type": "DEBIT",
            "purpose": f"Purchase: {house['house_name']}",
            "unit_sqm": house_plan.get("unitSqm", "N/A"),
            "house_image": first_house_img, 
            "status": "SUCCESS",
            "created_at": datetime.datetime.utcnow().isoformat()
        })

        
        logger.info(f"Purchase completed: user={user_id}, house={house['house_name']}, plan={purchase.plan_type}")


        # Send Email
        image_to_send = ""
        if house.get("house_image") and len(house["house_image"]) > 0:
            first_img = house["house_image"][0]
            if isinstance(first_img, list):
                # Legacy base64 chunks
                image_to_send = "".join(first_img)
            else:
                # URL path - convert to full absolute URL for email
                from utill import get_image_url
                image_to_send = get_image_url(first_img)


        background_tasks.add_task(
            send_purchase_email,
            user.get("email"),
            user.get("user_name"),
            house["house_name"],
            purchase.plan_type,
            purchase.amount_to_pay,
            portfolio_item["remaining_balance"],
            image_to_send
        )

        # Process Referral Activation
        process_referral_logic(user_id, purchase.amount_to_pay, user_collection, transactions_collection)
        
        return JSONResponse(status_code=200, content={"message": "Purchase successful! Your portfolio has been updated."})
        
    except Exception as e:
        logger.error(f"Purchase Error: {e}")
        return JSONResponse(status_code=500, content={"message": "An error occurred during purchase."})

@router.get("/my-portfolio")
def get_portfolio(data: dict = Depends(get_token)):
    try:
        user_id = data.get("id")
        if not user_id:
            return JSONResponse(status_code=401, content={"message": "Invalid user"})
        
        portfolio_data = list(portfolio_collection.find({"user_id": user_id}))
        from utill import get_image_url
        # Convert ObjectIds to strings and resolve images
        for item in portfolio_data:
            item["_id"] = str(item["_id"])
            
            # If house_image is missing or legacy, try to fetch current URL from house collection
            h_img = item.get("house_image")
            if not h_img or isinstance(h_img, list):
                h = house_collection.find_one({"_id": item.get("house_id")})
                if h and h.get("house_image") and len(h["house_image"]) > 0:
                    first_img = h["house_image"][0]
                    if isinstance(first_img, str) and not first_img.startswith("data:"):
                        item["house_image"] = get_image_url(first_img)
                    else:
                        item["house_image"] = ""
                else:
                    item["house_image"] = ""
            elif isinstance(h_img, str) and not h_img.startswith("data:"):
                item["house_image"] = get_image_url(h_img)
            
        return JSONResponse(status_code=200, content={"message": "Success", "data": portfolio_data})


    except Exception as e:
        logger.error(f"Portfolio Fetch Error: {e}")
        return JSONResponse(status_code=500, content={"message": "Could not fetch portfolio"})

@router.post("/pay-installment/{portfolio_id}")
def pay_installment(portfolio_id: str, background_tasks: BackgroundTasks, data: dict = Depends(get_token)):
    try:
        user_id = data.get("id")
        if not user_id:
            return JSONResponse(status_code=401, content={"message": "Invalid user"})

        user = user_collection.find_one({"_id": user_id})
        portfolio = portfolio_collection.find_one({"_id": portfolio_id, "user_id": user_id})

        if not portfolio or portfolio["status"] == "Completed":
            return JSONResponse(status_code=404, content={"message": "Active portfolio not found"})

        monthly_due = portfolio["monthly_payment"]
        current_balance = float(user.get("wallet_balance", 0.0))

        if current_balance < monthly_due:
            return JSONResponse(status_code=400, content={"message": "Insufficient wallet balance to pay monthly installment. Please top up."})

        # Calculate everything BEFORE updating any records to prevent partial failure
        try:
            months_paid = portfolio["months_paid"] + 1
            remaining_balance = portfolio["remaining_balance"] - monthly_due
            
            status = "Completed" if months_paid >= portfolio["duration_months"] or remaining_balance <= 0.5 else "Active"
            
            # Robust date parsing
            next_date = ""
            if status == "Active":
                try:
                    # fromisoformat is much more robust than strptime for ISO strings
                    current_next_date = datetime.datetime.fromisoformat(portfolio["next_payment_date"])
                    next_date = (current_next_date + relativedelta(months=1)).isoformat()
                except (ValueError, TypeError, KeyError):
                    # Fallback if date is missing or malformed
                    next_date = (datetime.datetime.utcnow() + relativedelta(months=1)).isoformat()

            # 1. Update portfolio (Progress update)
            portfolio_collection.update_one(
                {"_id": portfolio_id},
                {"$set": {
                    "months_paid": months_paid,
                    "remaining_balance": remaining_balance,
                    "amount_paid": portfolio["amount_paid"] + monthly_due,
                    "status": status,
                    "next_payment_date": next_date
                }}
            )
            logger.info(f"STEP 1 COMPLETE: Portfolio updated for {portfolio_id}")

            # 2. Extract SQM and skip large image for installment history (prevents indexing crash)
            h_data = house_collection.find_one({"_id": portfolio["house_id"]})
            u_sqm = portfolio.get("unit_sqm", "N/A")
            if h_data and u_sqm == "N/A":
                u_sqm = h_data.get("house_pricing_plan", [{}])[0].get("unitSqm", "N/A")

            # 3. Record Transaction for History (without large image)
            transactions_collection.insert_one({
                "tx_ref": f"PAY-{secrets.token_hex(6).upper()}",
                "user_id": user_id,
                "amount": monthly_due,
                "gateway": "Wallet",
                "type": "DEBIT",
                "purpose": f"Installment: {portfolio['house_name']}",
                "unit_sqm": u_sqm,
                "house_image": "", # Skip image here to avoid database indexing limits
                "status": "SUCCESS",
                "created_at": datetime.datetime.utcnow().isoformat()
            })
            logger.info(f"STEP 2 COMPLETE: Transaction recorded for {user_id}")
            
            # 4. Update wallet (The final, critical step)
            new_balance = current_balance - monthly_due
            user_collection.update_one({"_id": user_id}, {"$set": {"wallet_balance": new_balance}})
            logger.info(f"STEP 3 COMPLETE: Wallet deducted for {user_id}. New Balance: {new_balance}")

            # 5. Send receipt email
            background_tasks.add_task(
                send_purchase_email,
                user.get("email"),
                user.get("user_name"),
                portfolio["house_name"],
                "installment continuation",
                monthly_due,
                remaining_balance,
                "" # skip image for recurring
            )

            return JSONResponse(status_code=200, content={"message": "Installment payment successful!"})

        except Exception as inner_e:
            logger.error(f"Logic Error in Pay Installment (Step failure): {inner_e}")
            return JSONResponse(status_code=500, content={"message": "Internal error processing payment logic"})

    except Exception as e:
        logger.error(f"Pay Installment Error: {e}")
        return JSONResponse(status_code=500, content={"message": "Could not process installment payment"})
