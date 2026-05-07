from fastapi import APIRouter, Depends, HTTPException, status, Request, File, UploadFile, Form
from db.database import user_collection, transactions_collection
from utill import get_token, process_referral_logic
from utils.payment_gateways.factory import PaymentGatewayFactory
import secrets
import datetime
import calendar
from fastapi.responses import JSONResponse, HTMLResponse
import logging

logger = logging.getLogger("jardx")

router = APIRouter(prefix="/users/payment", tags=["Payment"])

@router.post("/initialize-payment")
async def initialize_payment(payload: dict, data: dict = Depends(get_token)):
    user_id = data.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid user")

    amount = payload.get("amount")
    gateway_name = payload.get("gateway") # "Monnify" or "Flutterwave"
    redirect_url = payload.get("redirect_url")

    if not amount or not gateway_name:
        raise HTTPException(status_code=400, detail="Amount and gateway are required")

    # Fetch user details for the gateway
    user = user_collection.find_one({"_id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Generate unique reference
    tx_ref = f"JDX-{secrets.token_hex(6).upper()}"

    try:
        gateway = PaymentGatewayFactory.get_gateway(gateway_name)
        init_response = await gateway.initialize_payment(
            amount=float(amount),
            user_email=user["email"],
            reference=tx_ref,
            user_name=user.get("user_name", "JardX User"),
            redirect_url=redirect_url
        )

        # Record transaction as PENDING
        transactions_collection.insert_one({
            "tx_ref": tx_ref,
            "user_id": user_id,
            "amount": float(amount),
            "gateway": gateway_name,
            "status": "PENDING",
            "created_at": datetime.datetime.utcnow().isoformat()
        })

        return {
            "status": "success",
            "data": init_response
        }

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"Payment Init Error Details:\n{error_details}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/verify-transaction/{tx_ref}")
async def verify_transaction(tx_ref: str, data: dict = Depends(get_token)):
    user_id = data.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid user")

    # 1. Find the transaction and check if ALREADY credited
    transaction = transactions_collection.find_one({"tx_ref": tx_ref})
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")

    if transaction["status"] == "SUCCESS":
        return {"status": "success", "message": "Transaction already verified and wallet credited"}

    # 2. Check with the gateway
    gateway_name = transaction["gateway"]
    try:
        gateway = PaymentGatewayFactory.get_gateway(gateway_name)
        verify_response = await gateway.verify_transaction(tx_ref)
        
        # Log Gateway Response for debugging
        logger.info(f"Gateway ({gateway_name}) response for {tx_ref}: {verify_response}")
        
        is_paid = False
        if gateway_name == "Monnify":
            # Monnify PAID status check
            response_body = verify_response.get("responseBody") or {}
            is_paid = response_body.get("paymentStatus") == "PAID"
        elif gateway_name == "Flutterwave":
            # Flutterwave successful status check
            status_ok = verify_response.get("status") == "success"
            data_node = verify_response.get("data") or {}
            is_paid = status_ok and data_node.get("status") == "successful"

        if is_paid:
            # 3. Double-check status inside the "Paid" block for atomicity
            current_tx = transactions_collection.find_one({"tx_ref": tx_ref})
            if current_tx["status"] == "SUCCESS":
                return {"status": "success", "message": "Transaction already verified"}

            # Update transaction status FIRST
            transactions_collection.update_one(
                {"tx_ref": tx_ref},
                {"$set": {"status": "SUCCESS", "completed_at": datetime.datetime.utcnow().isoformat()}}
            )
            
            user_id = transaction["user_id"]
            user = user_collection.find_one({"_id": user_id})
            old_balance = float(user.get("wallet_balance", 0))
            new_balance = old_balance + float(transaction["amount"])
            user_collection.update_one({"_id": user_id}, {"$set": {"wallet_balance": new_balance}})
            
            logger.info(f"Wallet Credited: {user_id} | Ref: {tx_ref} | Old: {old_balance} | New: {new_balance}")
            
            # Process Referral Activation & Bonus
            process_referral_logic(user_id, float(transaction["amount"]), user_collection, transactions_collection)
            
            return {"status": "success", "message": "Payment verified and wallet credited"}
        else:
            # Handle non-success states and detect explicit failures
            gateway_status = "PENDING"
            is_failed = False
            
            if gateway_name == "Monnify":
                response_body = verify_response.get("responseBody") or {}
                gateway_status = response_body.get("paymentStatus", "PENDING")
                if gateway_status.upper() in ["FAILED", "CANCELLED", "EXPIRED", "ABANDONED"]:
                    is_failed = True
            elif gateway_name == "Flutterwave":
                data_node = verify_response.get("data") or {}
                gateway_status = data_node.get("status", "pending")
                if gateway_status.lower() in ["failed", "cancelled", "error"]:
                    is_failed = True
                if verify_response.get("status") == "error":
                    is_failed = True
                    gateway_status = verify_response.get("message", "error")

            if is_failed:
                transactions_collection.update_one(
                    {"tx_ref": tx_ref},
                    {"$set": {"status": "FAILED", "completed_at": datetime.datetime.utcnow().isoformat()}}
                )
                logger.warning(f"Payment FAILED for {tx_ref}. Status: {gateway_status}")
                return {"status": "failed", "message": f"Payment failed: {gateway_status}"}

            logger.info(f"Payment still pending for {tx_ref}. Status: {gateway_status}")
            return {"status": "pending", "message": f"Payment status: {gateway_status}"}

    except Exception as e:
        import traceback
        logger.error(f"Verification Error for {tx_ref}:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Error verifying transaction: {str(e)}")

@router.post("/webhook/{provider}")
async def handle_payment_webhook(provider: str, request: Request):
    payload = await request.json()
    raw_body = await request.body()
    signature = request.headers.get("x-monnify-signature") or request.headers.get("verif-hash")
    
    try:
        gateway = PaymentGatewayFactory.get_gateway(provider.capitalize() if provider == "monnify" else "Flutterwave")
        is_valid = await gateway.handle_webhook(payload, signature, raw_body=raw_body)
        
        if is_valid:
            # Extract reference from payload (gateway specific)
            ref = payload.get("paymentReference") or payload.get("tx_ref")
            
            # Find and update transaction
            transaction = transactions_collection.find_one({"tx_ref": ref})
            if transaction and transaction["status"] == "PENDING":
                transactions_collection.update_one(
                    {"tx_ref": ref},
                    {"$set": {"status": "SUCCESS", "completed_at": datetime.datetime.utcnow().isoformat()}}
                )
                
                # Credit the user wallet
                user_id = transaction["user_id"]
                user = user_collection.find_one({"_id": user_id})
                new_balance = float(user.get("wallet_balance", 0)) + float(transaction["amount"])
                user_collection.update_one({"_id": user_id}, {"$set": {"wallet_balance": new_balance}})
                
                # Process Referral Activation & Bonus
                process_referral_logic(user_id, float(transaction["amount"]), user_collection, transactions_collection)
                
                return {"status": "success", "message": "Wallet credited"}
                
        return {"status": "ignored", "message": "Signature invalid or status not successful"}
        
    except Exception as e:
        logger.error(f"Webhook Error: {str(e)}")
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})


@router.get("/history")
async def get_payment_history(
    month: int = None, 
    year: int = None, 
    status_filter: str = None,
    purpose_filter: str = None,
    page: int = 1,
    limit: int = 50,
    data: dict = Depends(get_token)
):
    """
    Organized transaction history with filtering and pagination.
    """
    user_id = data.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid user")

    try:
        # Base Query
        query = {"user_id": user_id}
        
        # 1. Date Filtering
        if (month is not None) and (year is not None):
            start_date = f"{year}-{month:02d}-01"
            last_day = calendar.monthrange(year, month)[1]
            end_date = f"{year}-{month:02d}-{last_day}T23:59:59"
            query["created_at"] = {"$gte": start_date, "$lte": end_date}
        elif year:
            start_date = f"{year}-01-01"
            end_date = f"{year}-12-31T23:59:59"
            query["created_at"] = {"$gte": start_date, "$lte": end_date}

        # 2. Status Filtering (Backend mapping)
        if status_filter and status_filter != "All":
            status_map = {
                "Successful": ["SUCCESSFUL", "SUCCESS"],
                "Failed": ["FAILED", "DECLINED"],
                "In Progress": ["PENDING", "PROCESSING"]
            }
            if status_filter in status_map:
                query["status"] = {"$in": status_map[status_filter]}

        # 3. Purpose/Category Filtering
        if purpose_filter and purpose_filter != "All":
            # Map frontend labels to backend search terms
            if purpose_filter == "JardHouz":
                query["purpose"] = {"$regex": "Purchase|Installment", "$options": "i"}
            elif purpose_filter == "Referral bonus":
                query["purpose"] = {"$regex": "Referral", "$options": "i"}
            else:
                query["purpose"] = {"$regex": purpose_filter, "$options": "i"}

        # 4. Pagination & Execution
        skip = (page - 1) * limit if page > 0 else 0
        try:
            total_count = transactions_collection.count_documents(query, upper_bound=1000)
        except:
            total_count = len(list(transactions_collection.find(query, projection={"_id": 1})))
        
        cursor = transactions_collection.find(
            query,
            sort={"created_at": -1},
            limit=limit,
            skip=skip
        )
        transactions = list(cursor)
        
        # 5. Normalization & Enrichment
        now = datetime.datetime.utcnow()
        for tx in transactions:
            tx["_id"] = str(tx["_id"])
            
            # Normalize status for UI
            db_status = tx.get("status", "PENDING").upper()
            if db_status in ["SUCCESSFUL", "SUCCESS"]:
                tx["status"] = "SUCCESSFUL"
            elif db_status in ["FAILED", "DECLINED"]:
                tx["status"] = "FAILED"
            else:
                # 🕰️ Auto-fail old pending transactions (1 hour limit)
                created_at = tx.get("created_at")
                if created_at:
                    try:
                        tx_time = datetime.datetime.fromisoformat(created_at)
                        if (now - tx_time).total_seconds() > 3600:
                            tx["status"] = "FAILED"
                        else:
                            tx["status"] = "PENDING"
                    except:
                        tx["status"] = "PENDING"
                else:
                    tx["status"] = "PENDING"

            # Add missing metadata defaults
            if "type" not in tx:
                tx["type"] = "CREDIT" if tx["status"] == "SUCCESSFUL" else "DEBIT"
            
            if "purpose" not in tx:
                tx["purpose"] = f"Wallet Funding ({tx.get('gateway', 'Unknown')})"
        
        return {
            "status": "success", 
            "data": transactions,
            "metadata": {
                "total_count": total_count,
                "page": page,
                "limit": limit,
                "pages": (total_count + limit - 1) // limit
            }
        }

    except Exception as e:
        import traceback
        logger.error(f"History Fetch Error: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Could not fetch transaction history")


@router.get("/callback")
async def payment_callback(
    paymentReference: str = None, 
    tx_ref: str = None, 
    status: str = None,
    redirect_url: str = None
):
    """
    This endpoint acts as a transition bridge for payment gateways.
    It now checks for success/failure to avoid redirecting canceled payments to the success screen.
    """
    # Determine reference
    ref = paymentReference or tx_ref
    
    # Check if we should redirect to success or failure
    # Monnify sends 'status=PAID' on success.
    # Flutterwave sends 'status=successful' or 'status=cancelled'.
    
    is_successful = False
    is_failed = False
    
    if status:
        if status.upper() in ["PAID", "SUCCESSFUL", "SUCCESS"]:
            is_successful = True
        elif status.upper() in ["FAILED", "CANCELLED", "EXPIRED", "ABANDONED"]:
            is_failed = True
    
    # If status is not clear from URL, check DB (webhook might have already updated it)
    if not is_successful and not is_failed and ref:
        transaction = transactions_collection.find_one({"tx_ref": ref})
        if transaction:
            # 🕵️ If still PENDING in DB, let's try an ACTIVE VERIFICATION right now
            # This handles cases where the webhook is slow or failed to reach us
            if transaction["status"] == "PENDING":
                try:
                    logger.info(f"Callback: Active verification for {ref}...")
                    # We can't use 'Depends(get_token)' here as it's a GET browser redirect
                    # But we have the user_id in the transaction record.
                    # We simulate a verify_transaction logic here
                    gateway = PaymentGatewayFactory.get_gateway(transaction["gateway"])
                    v_res = await gateway.verify_transaction(ref)
                    
                    gateway_ok = False
                    if transaction["gateway"] == "Monnify":
                        gateway_ok = (v_res.get("responseBody") or {}).get("paymentStatus") == "PAID"
                    elif transaction["gateway"] == "Flutterwave":
                        gateway_ok = v_res.get("status") == "success" and (v_res.get("data") or {}).get("status") == "successful"
                    
                    if gateway_ok:
                        # Success! Credit the user.
                        # (Note: we should use an atomic update or check if already credited)
                        # Re-fetching to be safe
                        tx_latest = transactions_collection.find_one({"tx_ref": ref})
                        if tx_latest["status"] == "PENDING":
                            transactions_collection.update_one(
                                {"tx_ref": ref},
                                {"$set": {"status": "SUCCESS", "completed_at": datetime.datetime.utcnow().isoformat()}}
                            )
                            user_id = tx_latest["user_id"]
                            user = user_collection.find_one({"_id": user_id})
                            new_bal = float(user.get("wallet_balance", 0)) + float(tx_latest["amount"])
                            user_collection.update_one({"_id": user_id}, {"$set": {"wallet_balance": new_bal}})
                            process_referral_logic(user_id, float(tx_latest["amount"]), user_collection, transactions_collection)
                            is_successful = True
                            logger.info(f"Callback: Successfully active-verified and credited {ref}")
                except Exception as e:
                    logger.error(f"Callback Verification failed for {ref}: {e}")

            # Re-check status after active verification attempt
            transaction = transactions_collection.find_one({"tx_ref": ref})
            if transaction["status"] == "SUCCESS":
                is_successful = True
            elif transaction["status"] == "FAILED":
                is_failed = True


    # Determine dynamic UI elements
    if is_successful:
        status_text = "Payment Successful!"
        status_icon = "✅"
        status_msg = "Your transaction was processed securely."
        theme_color = "#2BB12B"
    elif is_failed:
        status_text = "Payment Not Completed"
        status_icon = "❌"
        status_msg = "The transaction was canceled or did not go through."
        theme_color = "#EF4444"
    else:
        # Default/Verifying state
        status_text = "Verifying Transaction..."
        status_icon = "⏳"
        status_msg = "Please wait while we confirm your payment with the bank."
        theme_color = "#FF6900"

    # Final redirect URL based on outcome
    if not redirect_url:
        redirect_url = "jardx://addmoneysuccess" if is_successful else "jardx://paymentfailure"

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{status_text} - JardX</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; margin: 0; background-color: #f8fafc; color: #1e293b; text-align: center; padding: 20px; }}
            .card {{ background: white; padding: 40px; border-radius: 20px; shadow-box: 0 4px 6px -1px rgb(0 0 0 / 0.1); max-width: 400px; width: 100%; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1); }}
            .icon {{ font-size: 64px; margin-bottom: 20px; }}
            h2 {{ margin: 0 0 10px 0; color: #0f172a; }}
            p {{ color: #64748b; margin-bottom: 30px; line-height: 1.5; }}
            .btn {{ background-color: {theme_color}; color: white; padding: 16px 32px; border-radius: 12px; text-decoration: none; font-weight: bold; display: inline-block; transition: background-color 0.2s; }}
            .btn:hover {{ opacity: 0.9; }}
            .loader {{ border: 3px solid #f3f3f3; border-top: 3px solid {theme_color}; border-radius: 50%; width: 24px; height: 24px; animation: spin 1s linear infinite; display: inline-block; vertical-align: middle; margin-right: 10px; }}
            @keyframes spin {{ 0% {{ transform: rotate(0deg); }} 100% {{ transform: rotate(360deg); }} }}
        </style>
    </head>
    <body>
        <div class="card">
            <div class="icon">{status_icon}</div>
            <h2>{status_text}</h2>
            <p>{status_msg} We are taking you back to the JardX app.</p>
            
            <a href="{redirect_url}" id="redirect-link" class="btn">
                <div class="loader"></div>Return to App
            </a>
        </div>

        <script>
            // Automatically redirect after a short delay
            setTimeout(function() {{
                window.location.href = "{redirect_url}";
            }}, 2000);
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@router.post("/manual-transfer")
async def manual_transfer(
    amount: float = Form(...),
    proof: UploadFile = File(...),
    data: dict = Depends(get_token)
):
    user_id = data.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid user")

    try:
        # 1. Define storage path
        import os
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        timestamp = int(datetime.datetime.utcnow().timestamp())
        filename = f"manual_{user_id}_{timestamp}.jpg"
        filepath = os.path.join(base_dir, "uploads", "manual_payments", filename)
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        # 2. Save the proof file
        contents = await proof.read()
        with open(filepath, "wb") as f:
            f.write(contents)
            
        # 3. Create transaction record
        tx_ref = f"MANUAL-{secrets.token_hex(6).upper()}"
        proof_url = f"/uploads/manual_payments/{filename}"
        
        transactions_collection.insert_one({
            "tx_ref": tx_ref,
            "user_id": user_id,
            "amount": float(amount),
            "gateway": "Manual Bank Transfer",
            "type": "CREDIT",
            "purpose": "Bank Transfer (Manual Verification)",
            "status": "PENDING",
            "is_manual": True,
            "proof_url": proof_url,
            "created_at": datetime.datetime.utcnow().isoformat()
        })
        
        return {"status": "success", "message": "Transfer notification sent. It will be verified within 24-48 hours."}

    except Exception as e:
        logger.error(f"Manual Transfer Error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

