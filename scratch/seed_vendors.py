from astrapy import DataAPIClient
import os
import datetime
import uuid
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

print("Seeding vendors into database...")

# Clear existing test vendors if necessary, or just insert new ones
# For safety, let's just insert these profiles
mock_vendors = [
    {
        "_id": str(uuid.uuid4()),
        "fullName": "Tony Elumelu",
        "email": "tony@elumelu.com",
        "phone": "08012345678",
        "address": "Ogun State, Nigeria.",
        "profession": "Chef",
        "vocation": "Chef",
        "accountNumber": "1234567890",
        "bankName": "United Bank for Africa",
        "photo": "https://images.unsplash.com/photo-1577219491135-ce391730fb2c?q=80&w=300&auto=format&fit=crop",
        "certificate": "https://images.unsplash.com/photo-1589330273594-fade1ee91647?q=80&w=300&auto=format&fit=crop",
        "accountType": "vendor",
        "status": "verified",
        "rating": 4.5,
        "reviews_count": 132,
        "created_at": datetime.datetime.utcnow().isoformat()
    },
    {
        "_id": str(uuid.uuid4()),
        "fullName": "Tope Alabi",
        "email": "tope@alabi.com",
        "phone": "08087654321",
        "address": "Lagos State, Nigeria.",
        "profession": "Plumber",
        "vocation": "Plumber",
        "accountNumber": "0987654321",
        "bankName": "Zenith Bank",
        "photo": "https://images.unsplash.com/photo-1621905251189-08b45d6a269e?q=80&w=300&auto=format&fit=crop",
        "certificate": "https://images.unsplash.com/photo-1589330273594-fade1ee91647?q=80&w=300&auto=format&fit=crop",
        "accountType": "vendor",
        "status": "verified",
        "rating": 4.2,
        "reviews_count": 98,
        "created_at": datetime.datetime.utcnow().isoformat()
    },
    {
        "_id": str(uuid.uuid4()),
        "fullName": "Susan Bright",
        "email": "susan@bright.com",
        "phone": "09055554444",
        "address": "Abia State, Nigeria.",
        "profession": "Cook",
        "vocation": "Cook",
        "accountNumber": "1122334455",
        "bankName": "Access Bank",
        "photo": "https://images.unsplash.com/photo-1581299894007-aaa50297cf16?q=80&w=300&auto=format&fit=crop",
        "certificate": "https://images.unsplash.com/photo-1589330273594-fade1ee91647?q=80&w=300&auto=format&fit=crop",
        "accountType": "vendor",
        "status": "unverified",
        "rating": 4.8,
        "reviews_count": 132,
        "created_at": datetime.datetime.utcnow().isoformat()
    }
]

for vendor in mock_vendors:
    # Check if a vendor with this email already exists to prevent duplicate entries
    existing = vendors_collection.find_one({"email": vendor["email"]})
    if existing:
        print(f"Vendor with email {vendor['email']} already exists. Updating...")
        vendors_collection.update_one({"email": vendor["email"]}, {"$set": vendor})
    else:
        vendors_collection.insert_one(vendor)
        print(f"Inserted vendor: {vendor['fullName']}")

print("Seeding completed successfully!")
