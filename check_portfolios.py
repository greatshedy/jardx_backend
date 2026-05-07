import os
from dotenv import load_dotenv
from astrapy import DataAPIClient

load_dotenv("Backend/.env")

DB_TOKEN = os.getenv("DB_TOKEN")
DB_URL = os.getenv("DB_URL")

# Connect to Astra DB
client = DataAPIClient(DB_TOKEN)
db = client.get_database_by_api_endpoint(DB_URL)
portfolio_collection = db.get_collection("portfolio")
house_collection = db.get_collection("jard_houses")

# Fetch portfolios
portfolios = list(portfolio_collection.find({}))
print(f"Found {len(portfolios)} portfolios")

for p in portfolios:
    print(f"Portfolio ID: {p.get('_id')}")
    print(f"House Name: {p.get('house_name')}")
    print(f"House Image in Portfolio: {p.get('house_image')}")
    
    # Check house collection
    house_id = p.get('house_id')
    if house_id:
        house = house_collection.find_one({"_id": house_id})
        if house:
            print(f"House Image in House Collection: {house.get('house_image')}")
        else:
            print("House not found in house collection")
    print("-" * 20)
