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

def get_image_url(image_path: str) -> str:
    if not image_path:
        return ""
    if image_path.startswith("http") or image_path.startswith("data:") or "cloudinary" in image_path:
        return image_path
    return f"http://BACKEND/{image_path}"

# Simulate get_portfolio logic
def simulate_get_portfolio():
    # In a real scenario, we'd filter by user_id. Here we just take all for diagnostics.
    portfolio_data = list(portfolio_collection.find({}))
    
    results = []
    for item in portfolio_data:
        item["_id"] = str(item["_id"])
        
        # Logic from portfolio.py
        h_img = item.get("house_image")
        if not h_img or isinstance(h_img, list):
            h_id = item.get("house_id")
            print(f"DEBUG: Item {item.get('house_name')} has no image. Fetching house_id: {h_id}")
            h = house_collection.find_one({"_id": h_id})
            if h and h.get("house_image") and len(h["house_image"]) > 0:
                first_img = h["house_image"][0]
                print(f"DEBUG: Found house image: {first_img}")
                if isinstance(first_img, str) and not first_img.startswith("data:"):
                    item["house_image"] = get_image_url(first_img)
                else:
                    item["house_image"] = ""
            else:
                print(f"DEBUG: House not found or has no image for house_id: {h_id}")
                item["house_image"] = ""
        elif isinstance(h_img, str) and not h_img.startswith("data:"):
            item["house_image"] = get_image_url(h_img)
            
        results.append({
            "house_name": item.get("house_name"),
            "house_image": item.get("house_image")
        })
    return results

if __name__ == "__main__":
    final_data = simulate_get_portfolio()
    import json
    print(json.dumps(final_data, indent=2))
