from astrapy import DataAPIClient
import os
import datetime
import uuid
from dotenv import load_dotenv

load_dotenv()

db_token = os.getenv("DB_TOKEN")
db_url = os.getenv("DB_URL")

client = DataAPIClient(db_token)
db = client.get_database_by_api_endpoint(db_url)
vendors_collection = db.get_collection("vendors")

# Emails that were FAKE/seeded by us — these should be removed
fake_emails = ["tony@elumelu.com", "tope@alabi.com", "susan@bright.com"]

print("Removing fake seeded vendors...")
all_vendors = list(vendors_collection.find({}))
deleted = 0
for v in all_vendors:
    if v.get("email") in fake_emails:
        vendors_collection.delete_one({"_id": v["_id"]})
        print(f"  Deleted fake: {v.get('fullName')} ({v.get('email')})")
        deleted += 1

print(f"Removed {deleted} fake vendors.")

# Restore the real vendor "Timi" that was accidentally deleted
# Using the original data that was printed before it was deleted
real_vendor = {
    "_id": "14995b0a-474e-4772-995b-0a474e577226",
    "fullName": "Timi",
    "email": "greatboyshedy@gmail.com",
    "phone": "123456789",
    "address": "Gsgs hshs",
    "vocation": "Web developer",
    "accountNumber": "1234578690",
    "bankName": "Rubies MFB",
    "photo": "https://res.cloudinary.com/dbuzfzydb/image/upload/v1778177913/vendors/photos/352af721-4c8a-42cd-aaf7-214c8a82cd73/wmnuj8mj5npkhnxl2mrx.jpg",
    "certificate": "https://res.cloudinary.com/dbuzfzydb/image/upload/v1778177914/vendors/certs/352af721-4c8a-42cd-aaf7-214c8a82cd73/ynoxhtc9jbutxknn53ys.jpg",
    "user_id": "352af721-4c8a-42cd-aaf7-214c8a82cd73",
    "status": "unverified",
    "created_at": "2026-05-07T18:18:35.280069",
    "rating": 4.5,
    "reviews_count": 132,
}

existing = vendors_collection.find_one({"email": real_vendor["email"]})
if existing:
    print(f"Timi already exists in database. Skipping insert.")
else:
    vendors_collection.insert_one(real_vendor)
    print(f"Restored real vendor: Timi (greatboyshedy@gmail.com)")

print("\nFinal vendor list:")
for v in vendors_collection.find({}):
    print(f"  - {v.get('fullName')} | {v.get('email')} | {v.get('status')}")
