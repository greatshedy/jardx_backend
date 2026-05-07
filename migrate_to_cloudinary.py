import os
import logging
from db.database import user_collection, house_collection, portfolio_collection
from utill import upload_to_cloudinary, reassemble_base64_string
import base64

# Configure Logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("cloudinary_migration")

# Define paths
current_dir = os.path.dirname(os.path.abspath(__file__))

def migrate_to_cloudinary():
    logger.info("🚀 Starting Cloudinary Migration...")

    # 1. Migrate House Images
    logger.info("--- Processing Houses ---")
    houses = list(house_collection.find({}))
    for house in houses:
        house_id = house["_id"]
        house_name = house.get("house_name", "Unknown House")
        images = house.get("house_image", [])
        
        if not images:
            continue
            
        new_urls = []
        updated = False
        
        for i, img in enumerate(images):
            # Case 1: Already a Cloudinary URL
            if isinstance(img, str) and "cloudinary" in img:
                new_urls.append(img)
                continue
            
            # Case 2: Local path (/uploads/houses/...)
            if isinstance(img, str) and img.startswith("/uploads/"):
                local_path = os.path.join(current_dir, img.lstrip("/"))
                if os.path.exists(local_path):
                    logger.info(f"Uploading local image for {house_name}...")
                    url = upload_to_cloudinary(local_path, folder="houses")
                    if url:
                        new_urls.append(url)
                        updated = True
                    else:
                        new_urls.append(img) # Keep old if failed
                else:
                    logger.warning(f"File not found: {local_path}")
                    new_urls.append(img)
            
            # Case 3: Legacy Base64 or Chunked Data
            elif isinstance(img, (list, str)) and (isinstance(img, list) or img.startswith("data:") or len(img) > 500):
                logger.info(f"Uploading legacy base64 image for {house_name}...")
                try:
                    if isinstance(img, list):
                        data = reassemble_base64_string(img)
                    else:
                        data = img
                    
                    url = upload_to_cloudinary(data, folder="houses")
                    if url:
                        new_urls.append(url)
                        updated = True
                    else:
                        new_urls.append(img)
                except Exception as e:
                    logger.error(f"Failed to process base64 for {house_name}: {e}")
                    new_urls.append(img)
            else:
                new_urls.append(img)

        if updated:
            house_collection.update_one({"_id": house_id}, {"$set": {"house_image": new_urls}})
            logger.info(f"✅ Updated {house_name} with Cloudinary URLs.")

    # 2. Migrate User Profile Pics
    logger.info("--- Processing User Profiles ---")
    users = list(user_collection.find({"profile_pic": {"$exists": True, "$ne": ""}}))
    for user in users:
        user_id = user["_id"]
        user_name = user.get("user_name", "Unknown User")
        pic = user["profile_pic"]
        
        if "cloudinary" in pic or pic.startswith("http"):
            continue
            
        # Local path (/uploads/profiles/...)
        if pic.startswith("/uploads/"):
            local_path = os.path.join(current_dir, pic.lstrip("/"))
            if os.path.exists(local_path):
                logger.info(f"Uploading profile pic for {user_name}...")
                url = upload_to_cloudinary(local_path, folder="profiles")
                if url:
                    user_collection.update_one({"_id": user_id}, {"$set": {"profile_pic": url}})
                    logger.info(f"✅ Updated profile pic for {user_name}")
            else:
                logger.warning(f"File not found: {local_path}")

    # 3. Migrate Portfolio Images
    logger.info("--- Processing Portfolios ---")
    portfolios = list(portfolio_collection.find({"house_image": {"$exists": True, "$ne": ""}}))
    for item in portfolios:
        portfolio_id = item["_id"]
        house_name = item.get("house_name", "Unknown House")
        img = item["house_image"]
        
        if "cloudinary" in img or img.startswith("http"):
            continue
            
        # If it's a local path or legacy data
        if img.startswith("/uploads/"):
            local_path = os.path.join(current_dir, img.lstrip("/"))
            if os.path.exists(local_path):
                logger.info(f"Uploading portfolio image for {house_name}...")
                url = upload_to_cloudinary(local_path, folder="houses")
                if url:
                    portfolio_collection.update_one({"_id": portfolio_id}, {"$set": {"house_image": url}})
                    logger.info(f"✅ Updated portfolio for {house_name}")
            else:
                # If local file missing, try to get current URL from house collection
                h = house_collection.find_one({"house_name": house_name})
                if h and h.get("house_image") and "cloudinary" in h["house_image"][0]:
                    url = h["house_image"][0]
                    portfolio_collection.update_one({"_id": portfolio_id}, {"$set": {"house_image": url}})
                    logger.info(f"✅ Recovered portfolio image from House DB for {house_name}")

    logger.info("✨ Migration Complete!")

if __name__ == "__main__":
    migrate_to_cloudinary()
