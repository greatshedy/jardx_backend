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
        result = portfolio_collection.insert_one(portfolio_item)
        portfolio_id = str(result.inserted_id)

        # Record Transaction for History
        transactions_collection.insert_one({
            "tx_ref": f"BUY-{secrets.token_hex(6).upper()}",
            "user_id": user_id,
            "portfolio_id": portfolio_id, # Link to portfolio
            "amount": purchase.amount_to_pay,
            "gateway": "Wallet",
            "type": "DEBIT",
            "purpose": f"Purchase: {house['house_name']}",
            "unit_sqm": house_plan.get("unitSqm", "N/A"),
            "house_image": "", 
            "status": "SUCCESS",
            "created_at": datetime.datetime.utcnow().isoformat(),
            "months_covered": 0 # Initial payment
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

@router.post("/jardhouz/save")
def create_jardhouz_saving(saving_data: dict, background_tasks: BackgroundTasks, data: dict = Depends(get_token)):
    try:
        user_id = data.get("id")
        if not user_id:
            return JSONResponse(status_code=401, content={"message": "Invalid user"})
        
        user = user_collection.find_one({"_id": user_id})
        estate_name = saving_data.get("estate")
        target_amount = float(saving_data.get("target_amount", 0))
        duration = int(saving_data.get("duration", 0))
        frequency = saving_data.get("frequency")
        first_payment = float(saving_data.get("amount_to_pay", 0))

        if not user or first_payment <= 0:
            return JSONResponse(status_code=400, content={"message": "Invalid data or insufficient payment"})
        
        current_balance = float(user.get("wallet_balance", 0.0))
        if current_balance < first_payment:
            return JSONResponse(status_code=400, content={"message": "Insufficient wallet balance"})

        # Fetch house details for image
        house = house_collection.find_one({"house_name": estate_name})
        house_image = house.get("house_image", [])[0] if house and house.get("house_image") else ""

        # Create Portfolio Item (Saving Plan)
        portfolio_item = {
            "user_id": user_id,
            "house_name": estate_name,
            "house_id": str(house["_id"]) if house else None,
            "plan_type": "JardHouz Saving",
            "total_price": target_amount,
            "amount_paid": first_payment,
            "remaining_balance": target_amount - first_payment,
            "duration_months": duration,
            "frequency": frequency,
            "monthly_payment": first_payment, # The recurring amount
            "is_jardhouz_saving": True,
            "status": "Active",
            "house_image": house_image,
            "created_at": datetime.datetime.utcnow().isoformat(),
            "next_payment_date": (datetime.datetime.utcnow() + relativedelta(months=1)).isoformat()
        }

        # Record and deduct
        result = portfolio_collection.insert_one(portfolio_item)
        portfolio_id = str(result.inserted_id)
        user_collection.update_one({"_id": user_id}, {"$set": {"wallet_balance": current_balance - first_payment}})

        # Record Transaction
        transactions_collection.insert_one({
            "tx_ref": f"JH-SAVE-{secrets.token_hex(4).upper()}",
            "user_id": user_id,
            "portfolio_id": portfolio_id, # Link to portfolio
            "amount": first_payment,
            "gateway": "Wallet",
            "type": "DEBIT",
            "purpose": f"JardHouz Saving: {estate_name}",
            "status": "SUCCESS",
            "created_at": datetime.datetime.utcnow().isoformat(),
            "months_covered": 1
        })

        return JSONResponse(status_code=200, content={"message": "Saving plan started successfully!"})

    except Exception as e:
        logger.error(f"JH Save Error: {e}")
        return JSONResponse(status_code=500, content={"message": "Could not start saving plan"})

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

@router.get("/estate/{portfolio_id}")
def get_portfolio_item(portfolio_id: str, data: dict = Depends(get_token)):
    try:
        user_id = data.get("id")
        if not user_id:
            return JSONResponse(status_code=401, content={"message": "Invalid user"})
        
        item = portfolio_collection.find_one({"_id": portfolio_id, "user_id": user_id})
        if not item:
            return JSONResponse(status_code=404, content={"message": "Estate not found in your portfolio"})
        
        item["_id"] = str(item["_id"])
        
        # Fetch latest 5 transactions for this portfolio
        history = list(transactions_collection.find(
            {"portfolio_id": portfolio_id, "user_id": user_id},
            sort={"created_at": -1},
            limit=5
        ))
        for h_item in history:
            h_item["_id"] = str(h_item["_id"])
        
        item["payment_history"] = history
        
        # Resolve Image
        from utill import get_image_url
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
            
        return JSONResponse(status_code=200, content={"message": "Success", "data": item})

    except Exception as e:
        logger.error(f"Portfolio Item Fetch Error: {e}")
        return JSONResponse(status_code=500, content={"message": "Could not fetch estate details"})

@router.post("/pay-installment/{portfolio_id}")
def pay_installment(portfolio_id: str, background_tasks: BackgroundTasks, payload: dict = None, data: dict = Depends(get_token)):
    try:
        user_id = data.get("id")
        if not user_id:
            return JSONResponse(status_code=401, content={"message": "Invalid user"})

        user = user_collection.find_one({"_id": user_id})
        portfolio = portfolio_collection.find_one({"_id": portfolio_id, "user_id": user_id})

        if not portfolio or portfolio["status"] == "Completed":
            return JSONResponse(status_code=404, content={"message": "Active portfolio not found"})

        # Get months to pay from payload, default to 1
        num_months = 1
        if payload and "months_paid" in payload:
            num_months = int(payload["months_paid"])
        elif payload and "months" in payload: # Fallback for different naming
            num_months = int(payload["months"])

        monthly_base = float(portfolio.get("monthly_payment", 0))
        total_due = monthly_base * num_months
        current_balance = float(user.get("wallet_balance", 0.0))

        if current_balance < total_due:
            return JSONResponse(status_code=400, content={"message": f"Insufficient balance. You need ₦{total_due:,.2f} but have ₦{current_balance:,.2f}"})

        # Calculate everything BEFORE updating any records
        try:
            # Use .get() to avoid KeyError if 'months_paid' is missing
            current_months_paid = portfolio.get("months_paid", 0)
            new_months_paid = current_months_paid + num_months
            
            current_amount_paid = float(portfolio.get("amount_paid", 0))
            new_amount_paid = current_amount_paid + total_due
            
            remaining_balance = float(portfolio.get("remaining_balance", 0)) - total_due
            
            # Status check
            status = "Completed" if new_months_paid >= portfolio.get("duration_months", 24) or remaining_balance <= 0.5 else "Active"
            
            # Robust date parsing
            next_date = ""
            if status == "Active":
                try:
                    # Increment next payment date by the number of months paid
                    current_next_date = datetime.datetime.fromisoformat(portfolio.get("next_payment_date", datetime.datetime.utcnow().isoformat()))
                    next_date = (current_next_date + relativedelta(months=num_months)).isoformat()
                except (ValueError, TypeError, KeyError):
                    next_date = (datetime.datetime.utcnow() + relativedelta(months=num_months)).isoformat()

            # 1. Update portfolio
            portfolio_collection.update_one(
                {"_id": portfolio_id},
                {"$set": {
                    "months_paid": new_months_paid,
                    "remaining_balance": max(0, remaining_balance),
                    "amount_paid": new_amount_paid,
                    "status": status,
                    "next_payment_date": next_date
                }}
            )

            # 2. Record Transaction
            transactions_collection.insert_one({
                "tx_ref": f"PAY-{secrets.token_hex(6).upper()}",
                "user_id": user_id,
                "portfolio_id": portfolio_id, # Link to portfolio
                "amount": total_due,
                "gateway": "Wallet",
                "type": "DEBIT",
                "purpose": f"Installment ({num_months} mo): {portfolio['house_name']}",
                "unit_sqm": portfolio.get("unit_sqm", "N/A"),
                "house_image": "", 
                "status": "SUCCESS",
                "created_at": datetime.datetime.utcnow().isoformat(),
                "months_covered": num_months
            })
            
            # 3. Update wallet
            new_balance = current_balance - total_due
            user_collection.update_one({"_id": user_id}, {"$set": {"wallet_balance": new_balance}})

            # 4. Send receipt email
            background_tasks.add_task(
                send_purchase_email,
                user.get("email"),
                user.get("user_name"),
                portfolio["house_name"],
                f"installment ({num_months} months)",
                total_due,
                remaining_balance,
                ""
            )

            return JSONResponse(status_code=200, content={"message": "Installment payment successful!"})

        except Exception as inner_e:
            logger.error(f"Logic Error in Pay Installment (Step failure): {inner_e}")
            return JSONResponse(status_code=500, content={"message": f"Internal logic error: {str(inner_e)}"})

    except Exception as e:
        logger.error(f"Pay Installment Error: {e}")
        return JSONResponse(status_code=500, content={"message": "Could not process installment payment"})
