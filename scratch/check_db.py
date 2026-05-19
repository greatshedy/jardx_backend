from astrapy import DataAPIClient
import os
from dotenv import load_dotenv

load_dotenv()

db_token = os.getenv("DB_TOKEN")
db_url = os.getenv("DB_URL")

client = DataAPIClient(db_token)
db = client.get_database_by_api_endpoint(db_url)

vendors_collection = db.get_collection("vendors")
partners_collection = db.get_collection("partners")

print("--- VENDORS ---")
vendors = list(vendors_collection.find({}))
for v in vendors:
    print(v)

print("\n--- PARTNERS ---")
partners = list(partners_collection.find({}))
for p in partners:
    print(p)
