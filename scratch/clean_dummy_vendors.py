from astrapy import DataAPIClient
import os
from dotenv import load_dotenv

load_dotenv()

db_token = os.getenv("DB_TOKEN")
db_url = os.getenv("DB_URL")

if not db_token or not db_url:
    print("Error: DB_TOKEN or DB_URL not found in environment.")
    exit(1)

client = DataAPIClient(db_token)
db = client.get_database_by_api_endpoint(db_url)
vendors_collection = db.get_collection("vendors")

print("Cleaning up dummy vendors from database...")

# Allow only these mockup-specific emails
allowed_emails = ["tony@elumelu.com", "tope@alabi.com", "susan@bright.com"]

# Fetch all vendors
all_vendors = list(vendors_collection.find({}))
deleted_count = 0

for vendor in all_vendors:
    email = vendor.get("email")
    if email not in allowed_emails:
        print(f"Deleting dummy vendor: {vendor.get('fullName', 'Unknown')} ({email})")
        vendors_collection.delete_one({"_id": vendor["_id"]})
        deleted_count += 1

print(f"Cleanup completed! Deleted {deleted_count} dummy vendors.")
