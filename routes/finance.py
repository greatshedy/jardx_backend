from fastapi import APIRouter, BackgroundTasks
from utill import send_wallet_credit_email
from db.database import transactions_collection, portfolio_collection, user_collection, jard_kidz_collection, house_collection
from fastapi.responses import JSONResponse
from starlette import status
from datetime import datetime
import logging
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("jardx")

router = APIRouter(prefix="/admin/finance", tags=["Admin Finance"])

# Global executor for parallelizing Astra DB calls (more efficient than creating per-request)
executor = ThreadPoolExecutor(max_workers=20)

@router.get("/summary")
async def get_finance_summary():
    try:
        start_time = datetime.now()
        current_year = start_time.year
        
        # Define worker functions for parallel execution
        def fetch_portfolios():
            return list(portfolio_collection.find({}, projection={"amount_paid": 1}))

        def fetch_users():
            return list(user_collection.find({}, projection={"wallet_balance": 1}))

        def fetch_pending():
            return transactions_collection.count_documents({"status": "PENDING"}, upper_bound=1000)

        def fetch_active_plans():
            return jard_kidz_collection.count_documents({"status": "Active"}, upper_bound=1000)

        def fetch_history():
            # Optimization: Only fetch successful transactions FROM THIS YEAR
            # This drastically reduces data transfer as the database grows
            year_start = f"{current_year}-01-01"
            return list(transactions_collection.find(
                {
                    "status": "SUCCESS",
                    "created_at": {"$gte": year_start}
                },
                projection={"amount": 1, "created_at": 1, "type": 1}
            ))

        # Run all queries in parallel using the global executor
        # Using executor.submit instead of a fresh pool context
        f_portfolios = executor.submit(fetch_portfolios)
        f_users = executor.submit(fetch_users)
        f_pending = executor.submit(fetch_pending)
        f_active = executor.submit(fetch_active_plans)
        f_history = executor.submit(fetch_history)

        # Gather results
        portfolios = f_portfolios.result()
        users = f_users.result()
        pending_count = f_pending.result()
        active_plans = f_active.result()
        all_history_tx = f_history.result()

        # --- Aggregation logic (CPU bound, fast) ---
        total_properties_revenue = sum(p.get('amount_paid', 0) for p in portfolios)
        total_properties_count = len(portfolios)
        total_wallet_balance = sum(u.get('wallet_balance', 0) for u in users)
        total_users_count = len(users)

        monthly_revenue = [0] * 12
        for tx in all_history_tx:
            tx_type = tx.get('type', 'DEPOSIT')
            if tx_type in ['DEBIT', 'WITHDRAWAL']:
                continue 

            created_at = tx.get('created_at')
            if created_at:
                try:
                    if hasattr(created_at, 'timestamp_ms'):
                        dt = datetime.fromtimestamp(created_at.timestamp_ms / 1000.0)
                    else:
                        dt = datetime.fromisoformat(str(created_at).replace('Z', '+00:00'))
                    
                    if dt.year == current_year:
                        monthly_revenue[dt.month - 1] += float(tx.get('amount', 0))
                except:
                    pass

        summary = {
            "total_properties_revenue": total_properties_revenue,
            "total_properties_count": total_properties_count,
            "total_wallet_balance": total_wallet_balance,
            "total_users_count": total_users_count,
            "pending_transactions_count": pending_count,
            "active_plans_count": active_plans,
            "revenue_history": monthly_revenue
        }

        duration = (datetime.now() - start_time).total_seconds()
        logger.info(f"Dashboard summary Fetched in {duration:.2f}s")
        
        return JSONResponse({"message": "Finance summary", "data": summary, "status": 200})
    except Exception as e:
        logger.error(f"Error in dashboard summary: {e}")
        return JSONResponse(content={"message": str(e)}, status_code=500)

def serialize_astra_data(data):
    if isinstance(data, list):
        return [serialize_astra_data(i) for i in data]
    elif isinstance(data, dict):
        return {k: serialize_astra_data(v) for k, v in data.items()}
    elif hasattr(data, 'isoformat'): 
        return data.isoformat()
    elif hasattr(data, '__str__') and 'DataAPI' in str(type(data)):
        return str(data)
    else:
        return data

@router.get("/transactions")
async def get_all_transactions(page: int = 1, page_size: int = 15):
    try:
        skip = (page - 1) * page_size if page > 0 else 0
        try:
            total_count = transactions_collection.count_documents({}, upper_bound=10000)
        except:
            total_count = len(list(transactions_collection.find({}, projection={"_id": 1})))
            
        total_pages = (total_count + page_size - 1) // page_size

        cursor = transactions_collection.find(
            {}, 
            sort={"created_at": -1},
            limit=page_size,
            skip=skip
        )
        all_transactions = list(cursor)
        
        users_cursor = user_collection.find({}, projection={"user_name": 1, "email": 1})
        users_map = {str(u['_id']): {"name": u.get('user_name', 'Unknown User'), "email": u.get('email', '')} for u in users_cursor}
        
        enriched_transactions = []
        for tx in all_transactions:
            tx_clean = serialize_astra_data(tx)
            tx_clean['_id'] = str(tx_clean['_id'])
            user_id = str(tx_clean.get('user_id'))
            user_info = users_map.get(user_id, {"name": "Unknown User", "email": ""})
            tx_clean['user_name'] = user_info["name"]
            tx_clean['user_email'] = user_info["email"]
            
            # Ensure proof_url is absolute for the admin dashboard
            if tx_clean.get("proof_url") and not tx_clean["proof_url"].startswith("http"):
                from utill import get_image_url
                tx_clean["proof_url"] = get_image_url(tx_clean["proof_url"])
                
            enriched_transactions.append(tx_clean)

        logger.info(f"Fetched transactions page {page}")
        
        return JSONResponse({
            "message": "Transactions history", 
            "data": enriched_transactions, 
            "pagination": {
                "total_count": total_count,
                "total_pages": total_pages,
                "current_page": page,
                "page_size": page_size
            },
            "status": status.HTTP_200_OK
        })
    except Exception as e:
        import traceback
        logger.error(f"Error fetching transactions: {traceback.format_exc()}")
        return JSONResponse({"message": f"Error: {str(e)}", "status": status.HTTP_500_INTERNAL_SERVER_ERROR})


@router.post("/approve-transaction/{tx_ref}")
async def approve_transaction(tx_ref: str, background_tasks: BackgroundTasks):
    try:
        transaction = transactions_collection.find_one({"tx_ref": tx_ref})
        if not transaction:
            return JSONResponse({"message": "Transaction not found", "status": 404})
        
        if transaction["status"] == "SUCCESS":
            return JSONResponse({"message": "Transaction already approved", "status": 400})

        transactions_collection.update_one(
            {"tx_ref": tx_ref},
            {"$set": {"status": "SUCCESS", "completed_at": datetime.utcnow().isoformat()}}
        )

        user_id = transaction["user_id"]
        user = user_collection.find_one({"_id": user_id})
        if user:
            amount = float(transaction["amount"])
            new_balance = float(user.get("wallet_balance", 0)) + amount
            user_collection.update_one({"_id": user_id}, {"$set": {"wallet_balance": new_balance}})
            logger.info(f"Manual Approval: Wallet Credited for {user_id}. Ref: {tx_ref}")

            # Referral Logic
            from utill import process_referral_logic
            process_referral_logic(user_id, amount, user_collection, transactions_collection)

            # Send Email
            background_tasks.add_task(
                send_wallet_credit_email,
                user.get("email"),
                user.get("user_name", "User"),
                amount,
                new_balance
            )

        return JSONResponse({"message": "Transaction approved and wallet credited", "status": 200})
    except Exception as e:
        logger.error(f"Error approving transaction {tx_ref}: {e}")
        return JSONResponse({"message": str(e), "status": 500})


@router.post("/decline-transaction/{tx_ref}")
async def decline_transaction(tx_ref: str):
    try:
        transactions_collection.update_one(
            {"tx_ref": tx_ref},
            {"$set": {"status": "FAILED", "completed_at": datetime.utcnow().isoformat()}}
        )
        return JSONResponse({"message": "Transaction declined", "status": 200})
    except Exception as e:
        logger.error(f"Error declining transaction {tx_ref}: {e}")
        return JSONResponse({"message": str(e), "status": 500})


@router.post("/processing-transaction/{tx_ref}")
async def set_processing(tx_ref: str):
    try:
        transactions_collection.update_one(
            {"tx_ref": tx_ref},
            {"$set": {"status": "PROCESSING"}}
        )
        return JSONResponse({"message": "Status set to PROCESSING", "status": 200})
    except Exception as e:
        logger.error(f"Error setting status to processing for {tx_ref}: {e}")
        return JSONResponse({"message": str(e), "status": 500})

@router.get("/reports")
async def get_admin_reports():
    try:
        def fetch_all_portfolios():
            return list(portfolio_collection.find({}, projection={"house_id": 1, "amount_paid": 1, "paid_percentage": 1}))

        def fetch_all_houses():
            return list(house_collection.find({}, projection={"_id": 1, "name": 1, "property_name": 1}))

        def fetch_all_users():
            return list(user_collection.find({}, projection={"_id": 1, "created_at": 1, "wallet_balance": 1, "user_name": 1}))

        def fetch_all_plans():
            return list(jard_kidz_collection.find({}, projection={"status": 1, "total_balance": 1}))

        def fetch_tx_health():
            # Get counts for status success vs failed vs pending
            pipeline = [
                {"$group": {"_id": "$status", "count": {"$sum": 1}}}
            ]
            # Astra Data API group is currently through client side or specific aggregate.
            # We'll do it client side for maximum compatibility with current setup.
            return list(transactions_collection.find({}, projection={"status": 1}))

        with ThreadPoolExecutor(max_workers=5) as pool:
            f_portfolios = pool.submit(fetch_all_portfolios)
            f_houses = pool.submit(fetch_all_houses)
            f_users = pool.submit(fetch_all_users)
            f_plans = pool.submit(fetch_all_plans)
            f_tx = pool.submit(fetch_tx_health)

            portfolios = f_portfolios.result()
            houses = f_houses.result()
            users = f_users.result()
            plans = f_plans.result()
            transactions = f_tx.result()

        # 1. Property Revenue Leaderboard
        houses_map = {str(h['_id']): h.get('name') or h.get('property_name', 'Unnamed Property') for h in houses}
        prop_stats = {}
        for p in portfolios:
            hid = str(p.get('house_id'))
            if hid not in prop_stats:
                prop_stats[hid] = {"name": houses_map.get(hid, "Unknown"), "revenue": 0, "units": 0}
            prop_stats[hid]["revenue"] += float(p.get('amount_paid', 0))
            prop_stats[hid]["units"] += 1
        
        property_leaderboard = sorted(prop_stats.values(), key=lambda x: x['revenue'], reverse=True)[:5]

        # 2. User Growth Trend (Last 12 Months)
        user_trends = [0] * 12
        current_year = datetime.now().year
        for u in users:
            c_at = u.get('created_at')
            if c_at:
                try:
                    dt = datetime.fromisoformat(str(c_at).replace('Z', '+00:00'))
                    if dt.year == current_year:
                        user_trends[dt.month - 1] += 1
                except: pass

        # 3. Revenue Source Breakdown
        estate_revenue = sum(float(p.get('amount_paid', 0)) for p in portfolios)
        investment_revenue = sum(float(pl.get('total_balance', 0)) for pl in plans)

        # 4. Transaction Health
        tx_stats = {"SUCCESS": 0, "FAILED": 0, "PENDING": 0, "PROCESSING": 0}
        for tx in transactions:
            stat = tx.get('status', 'PENDING')
            if stat in tx_stats: tx_stats[stat] += 1

        # Extract top 4 investor names for initials
        top_investor_initials = [u.get('user_name', 'U')[:1].upper() for u in users[:4]]

        reports_data = {
            "property_leaderboard": property_leaderboard,
            "user_growth_trends": user_trends,
            "revenue_sources": {
                "real_estate": estate_revenue,
                "investments": investment_revenue
            },
            "transaction_health": tx_stats,
            "total_investors": len(plans),
            "top_investor_initials": top_investor_initials,
            "avg_wallet_balance": sum(float(u.get('wallet_balance', 0)) for u in users) / len(users) if users else 0
        }

        return JSONResponse({"message": "Reports generated", "data": reports_data, "status": 200})
    except Exception as e:
        logger.error(f"Error generating reports: {e}")
        return JSONResponse(content={"message": str(e)}, status_code=500)
