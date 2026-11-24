from fastapi import FastAPI,Depends,HTTPException,status,BackgroundTasks
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from astrapy import DataAPIClient
from astrapy.api_options import APIOptions, TimeoutOptions
from astrapy.info import CollectionDefinition, CollectionLexicalOptions
from dotenv import load_dotenv
import os
from model import User, Login, JardAccount
from utill import hashedpassword,VerifyHashed,decode_access_token,generate_otp,send_email,create_access_token




# Use absolute path to ensure .env is loaded regardless of current working directory
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
load_dotenv(os.path.join(backend_dir, ".env"))

db_token=os.getenv("DB_TOKEN")
db_url=os.getenv("DB_URL")

# Initialize the client with increased timeout
client = DataAPIClient(
    db_token,
    api_options=APIOptions(
        timeout_options=TimeoutOptions(
            request_timeout_ms=30000,
        ),
    ),
)
db = client.get_database_by_api_endpoint(db_url)

# --- Auxiliary database (separate keyspace to stay within the 100-index limit) ---
db_aux_token = os.getenv("DB_AUX_TOKEN")
db_aux_url = os.getenv("DB_AUX_URL")
if not db_aux_token or not db_aux_url:
    raise RuntimeError("DB_AUX_TOKEN and DB_AUX_URL must be set in .env")
aux_client = DataAPIClient(
    db_aux_token,
    api_options=APIOptions(
        timeout_options=TimeoutOptions(
            request_timeout_ms=30000,
        ),
    ),
)
db_aux = aux_client.get_database_by_api_endpoint(db_aux_url)

# Access collections
user_collection=db.get_collection("users")
house_collection=db.get_collection("jard_houses")
transactions_collection = db.get_collection("payment_transactions")
portfolio_collection = db.get_collection("portfolio")
jard_kidz_collection = db.get_collection("jard_kidz_plans")
# Ensure vendors collection exists
existing_collections = db.list_collection_names()
if "vendors" not in existing_collections:
    print("Collection 'vendors' not found. Creating...")
    try:
        vendors_collection = db.create_collection("vendors")
    except Exception as e:
        print(f"Error creating vendors collection: {e}")
        vendors_collection = db.get_collection("vendors")
else:
    vendors_collection = db.get_collection("vendors")

if "partners" not in existing_collections:
    print("Collection 'partners' not found. Creating...")
    try:
        partners_collection = db.create_collection("partners")
    except Exception as e:
        print(f"Error creating partners collection: {e}")
        partners_collection = db.get_collection("partners")
else:
    partners_collection = db.get_collection("partners")

# JardProc Collections
if "products" not in existing_collections:
    products_collection = db.create_collection("products")
else:
    products_collection = db.get_collection("products")

if "orders" not in existing_collections:
    orders_collection = db.create_collection("orders")
else:
    orders_collection = db.get_collection("orders")

if "reviews" not in existing_collections:
    reviews_collection = db.create_collection("reviews")
else:
    reviews_collection = db.get_collection("reviews")

unindexed_definition = CollectionDefinition(
    lexical=CollectionLexicalOptions(enabled=False),
    indexing={"deny": ["*"]},
)

if "distributors" not in existing_collections:
    try:
        distributors_collection = db.create_collection("distributors", definition=unindexed_definition)
    except Exception as e:
        print(f"Error creating distributors collection: {e}")
        distributors_collection = db.get_collection("distributors")
else:
    distributors_collection = db.get_collection("distributors")

if "housing_subscriptions" not in existing_collections:
    try:
        housing_subscriptions_collection = db.create_collection(
            "housing_subscriptions",
            definition=unindexed_definition,
        )
    except Exception as e:
        print(f"Error creating housing_subscriptions collection: {e}")
        housing_subscriptions_collection = db.get_collection("housing_subscriptions")
else:
    housing_subscriptions_collection = db.get_collection("housing_subscriptions")

# --- Jard Block collections (aux keyspace, intentionally unindexed) ---
_aux_collections = db_aux.list_collection_names()
if "block_plans" not in _aux_collections:
    try:
        block_plans_collection = db_aux.create_collection("block_plans", definition=unindexed_definition)
    except Exception as e:
        print(f"Error creating block_plans collection: {e}")
        block_plans_collection = db_aux.get_collection("block_plans")
else:
    block_plans_collection = db_aux.get_collection("block_plans")

if "block_investments" not in _aux_collections:
    try:
        block_investments_collection = db_aux.create_collection("block_investments", definition=unindexed_definition)
    except Exception as e:
        print(f"Error creating block_investments collection: {e}")
        block_investments_collection = db_aux.get_collection("block_investments")
else:
    block_investments_collection = db_aux.get_collection("block_investments")

if "block_referrals" not in _aux_collections:
    try:
        block_referrals_collection = db_aux.create_collection("block_referrals", definition=unindexed_definition)
    except Exception as e:
        print(f"Error creating block_referrals collection: {e}")
        block_referrals_collection = db_aux.get_collection("block_referrals")
else:
    block_referrals_collection = db_aux.get_collection("block_referrals")

if "block_settings" not in _aux_collections:
    try:
        block_settings_collection = db_aux.create_collection("block_settings", definition=unindexed_definition)
    except Exception as e:
        print(f"Error creating block_settings collection: {e}")
        block_settings_collection = db_aux.get_collection("block_settings")
else:
    block_settings_collection = db_aux.get_collection("block_settings")


def _seed_block_defaults():
    import datetime as _dt
    now = _dt.datetime.utcnow().isoformat()
    try:
        existing = list(block_plans_collection.find({}))
    except Exception as e:
        print(f"Block plan seed skipped: {e}")
        return
    if not existing:
        defaults = [
            {
                "slug": "bronze",
                "name": "Bronze",
                "description": "The accessible entry point into Jard Block with steady monthly returns.",
                "unit_price": 5000.0,
                "units_per_slot": 1,
                "annual_roi": 12.0,
                "moratorium_months": 4,
                "payout_months": 12,
                "principal_return": True,
                "referral_rate": 0.01,
                "unlimited_slots": True,
                "total_slots": None,
                "active": True,
                "order": 1,
                "color": "#CD7F32",
                "benefits": ["12% annual ROI", "4-month moratorium", "12 monthly payouts", "Principal returned at maturity"],
                "created_at": now,
                "updated_at": now,
            },
            {
                "slug": "silver",
                "name": "Silver",
                "description": "A balanced plan with stronger returns for growing portfolios.",
                "unit_price": 25000.0,
                "units_per_slot": 10,
                "annual_roi": 24.0,
                "moratorium_months": 4,
                "payout_months": 12,
                "principal_return": True,
                "referral_rate": 0.0,
                "unlimited_slots": True,
                "total_slots": None,
                "active": True,
                "order": 2,
                "color": "#9CA3AF",
                "benefits": ["24% annual ROI", "4-month moratorium", "12 monthly payouts", "Principal returned at maturity"],
                "created_at": now,
                "updated_at": now,
            },
            {
                "slug": "platinum",
                "name": "Platinum",
                "description": "The premium Jard Block tier for high-value investors.",
                "unit_price": 50000.0,
                "units_per_slot": 100,
                "annual_roi": 36.0,
                "moratorium_months": 4,
                "payout_months": 12,
                "principal_return": True,
                "referral_rate": 0.025,
                "unlimited_slots": True,
                "total_slots": None,
                "active": True,
                "order": 3,
                "color": "#F59E0B",
                "benefits": ["36% annual ROI", "4-month moratorium", "12 monthly payouts", "Principal returned at maturity"],
                "created_at": now,
                "updated_at": now,
            },
        ]
        for plan in defaults:
            try:
                block_plans_collection.insert_one(plan)
            except Exception as e:
                print(f"Block plan seed insert failed: {e}")
    try:
        if not list(block_settings_collection.find({})):
            block_settings_collection.insert_one({
                "_id": "global",
                "bank_name": "GTBank",
                "account_name": "Jarvad Supplying In'l",
                "account_number": "0106896517",
                "transfer_instructions": "Transfer the exact investment amount to the account above, then upload your proof of payment. Use your full name as the transfer narration.",
                "disclaimer": "Jard Block is an investment product. Returns are projections based on the selected plan and are subject to the terms of your investment. Only invest funds you can commit for the full term.",
                "support_whatsapp": "2347085579245",
                "updated_at": now,
            })
    except Exception as e:
        print(f"Block settings seed skipped: {e}")


_seed_block_defaults()

if "reports" not in db_aux.list_collection_names():
    try:
        reports_collection = db_aux.create_collection("reports")
    except Exception as e:
        print(f"Error creating reports collection: {e}")
        reports_collection = db_aux.get_collection("reports")
else:
    reports_collection = db_aux.get_collection("reports")

if "notifications" not in existing_collections:
    notifications_collection = db.create_collection("notifications")
else:
    notifications_collection = db.get_collection("notifications")

if "subscription_settings" not in db_aux.list_collection_names():
    try:
        sub_settings_collection = db_aux.create_collection("subscription_settings")
        sub_settings_collection.insert_one({
            "vendor_fee": 25000,
            "partner_fee": 100000,
            "sub_duration_months": 12,
            "penalty_percentage": 5,
            "support_whatsapp": "2347085579245",
            "housing_registration_fee": 100000,
            "housing_savings_multiplier": 2.0
        })
    except Exception:
        sub_settings_collection = db_aux.get_collection("subscription_settings")
else:
    sub_settings_collection = db_aux.get_collection("subscription_settings")

if "withdraw_requests" not in db_aux.list_collection_names():
    try:
        withdraw_requests_collection = db_aux.create_collection("withdraw_requests")
    except Exception as e:
        print(f"Error creating withdraw_requests collection: {e}")
        withdraw_requests_collection = db_aux.get_collection("withdraw_requests")
else:
    withdraw_requests_collection = db_aux.get_collection("withdraw_requests")

if "deletion_requests" not in db_aux.list_collection_names():
    try:
        deletion_requests_collection = db_aux.create_collection("deletion_requests")
    except Exception as e:
        print(f"Error creating deletion_requests collection: {e}")
        deletion_requests_collection = db_aux.get_collection("deletion_requests")
else:
    deletion_requests_collection = db_aux.get_collection("deletion_requests")

if "admin_push_subscriptions" not in db_aux.list_collection_names():
    try:
        admin_push_subscriptions_collection = db_aux.create_collection("admin_push_subscriptions")
    except Exception as e:
        print(f"Error creating admin_push_subscriptions collection: {e}")
        try:
            admin_push_subscriptions_collection = db_aux.get_collection("admin_push_subscriptions")
        except Exception as e2:
            print(f"admin_push_subscriptions unavailable: {e2}")
            admin_push_subscriptions_collection = None
else:
    try:
        admin_push_subscriptions_collection = db_aux.get_collection("admin_push_subscriptions")
    except Exception as e:
        print(f"Error getting admin_push_subscriptions collection: {e}")
        admin_push_subscriptions_collection = None

print("Astra DB connection initialized successfully.")