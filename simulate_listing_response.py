import os
from dotenv import load_dotenv
from astrapy import DataAPIClient

load_dotenv("Backend/.env")

DB_TOKEN = os.getenv("DB_TOKEN")
DB_URL = os.getenv("DB_URL")

# Connect to Astra DB
client = DataAPIClient(DB_TOKEN)
db = client.get_database_by_api_endpoint(DB_URL)
house_collection = db.get_collection("jard_houses")

def get_image_url(image_path: str) -> str:
    if not image_path:
        return ""
    if image_path.startswith("http") or image_path.startswith("data:") or "cloudinary" in image_path:
        return image_path
    return f"http://BACKEND/{image_path}"

def simulate_listing():
    house_data = list(house_collection.find({}))
    data = []
    for house in house_data:
        new_images = []
        if house.get("house_image"):
            for img in house["house_image"]:
                new_images.append(get_image_url(img))
        house["house_image"] = new_images
        data.append({
            "house_name": house.get("house_name"),
            "house_image": house.get("house_image")
        })
    return data

if __name__ == "__main__":
    final_data = simulate_listing()
    import json
    print(json.dumps(final_data, indent=2))
