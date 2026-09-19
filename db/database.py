from fastapi import FastAPI,Depends,HTTPException,status,BackgroundTasks
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from astrapy import DataAPIClient
from astrapy.api_options import APIOptions, TimeoutOptions
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
            "penalty_percentage": 5
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

print("Astra DB connection initialized successfully.")