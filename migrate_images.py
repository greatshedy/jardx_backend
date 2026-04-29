import os
import base64
import secrets
from db.database import house_collection
from utill import reassemble_base64_string
import logging

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("migration")

# Define paths
current_dir = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(current_dir, "uploads", "houses")
os.makedirs(UPLOAD_DIR, exist_ok=True)

def migrate_images():
    logger.info("Starting image migration...")
    houses = list(house_collection.find({}))
    logger.info(f"Found {len(houses)} houses to process.")

    for house in houses:
        house_id = house["_id"]
        house_name = house.get("house_name", "unknown")
        logger.info(f"Processing house: {house_name} ({house_id})")

        new_image_urls = []
        images = house.get("house_image", [])

        # Check if already migrated (urls usually start with /uploads)
        if images and isinstance(images[0], str) and images[0].startswith("/uploads"):
            logger.info(f"House {house_name} already migrated. Skipping.")
            continue

        for i, img_data in enumerate(images):
            try:
                # Reassemble if it's chunked list, else use as is if it's a string
                if isinstance(img_data, list):
                    base64_str = reassemble_base64_string(img_data)
                elif isinstance(img_data, str):
                    base64_str = img_data
                else:
                    logger.warning(f"Unexpected image data type for {house_name} at index {i}. Skipping.")
                    continue

                # Remove header if present (e.g. data:image/jpeg;base64,)
                if "," in base64_str:
                    base64_str = base64_str.split(",")[1]

                # Decode
                image_bytes = base64.b64decode(base64_str)

                # Generate filename
                filename = f"{secrets.token_hex(8)}.jpg"
                filepath = os.path.join(UPLOAD_DIR, filename)

                # Save file
                with open(filepath, "wb") as f:
                    f.write(image_bytes)

                # Store relative URL
                url = f"/uploads/houses/{filename}"
                new_image_urls.append(url)
                logger.info(f"Saved image {i} for {house_name} as {filename}")

            except Exception as e:
                logger.error(f"Error processing image {i} for {house_name}: {e}")

        # Update database
        if new_image_urls:
            house_collection.update_one(
                {"_id": house_id},
                {"$set": {"house_image": new_image_urls}}
            )
            logger.info(f"Updated {house_name} with {len(new_image_urls)} image URLs.")
        else:
            logger.warning(f"No images processed for {house_name}.")

    logger.info("Migration complete.")

if __name__ == "__main__":
    migrate_images()
