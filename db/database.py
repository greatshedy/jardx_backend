from fastapi import FastAPI,Depends,HTTPException,status,BackgroundTasks
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from astrapy import DataAPIClient
from dotenv import load_dotenv
import os
from model import User,Login, VendorRegister
from utill import hashedpassword,VerifyHashed,decode_access_token,generate_otp,send_email,create_access_token




# Use absolute path to ensure .env is loaded regardless of current working directory
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
load_dotenv(os.path.join(backend_dir, ".env"))

db_token=os.getenv("DB_TOKEN")
db_url=os.getenv("DB_URL")

# Initialize the client
client = DataAPIClient(db_token)
db = client.get_database_by_api_endpoint(db_url)

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

print("Astra DB connection initialized successfully.")