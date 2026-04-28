import sys
import os

# Adjust paths manually because we're in /tmp
sys.path.append(r'c:\Users\NEW USER\Desktop\jardx\Backend')

import base64
from db.database import house_collection

ids = [
    "dd176498-e633-405d-9764-98e633105db1",
    "6c22eb9b-eac4-4a03-a2eb-9beac42a0319",
    "8d4c4ac3-3925-45bd-8c4a-c33925d5bd13"
]

for hid in ids:
    print(f"\n--- Checking ID: {hid} ---")
    data = house_collection.find_one({"_id": hid})
    if not data:
        print("House not found")
        continue

    img_list = data.get("house_image", [])
    print(f"Number of image entries: {len(img_list)}")
    
    if len(img_list) > 0:
        first_img = img_list[0]
        if isinstance(first_img, list):
            print(f"First image chunk count: {len(first_img)}")
            total_str = "".join(first_img)
        else:
            print("First image is a string directly")
            total_str = first_img

        print(f"Total string length: {len(total_str)}")
        print(f"First 50 chars: {total_str[:50]}")
        
        # Check if it starts with data:
        if total_str.startswith("data:"):
            print("Found Data URI prefix")
        
        # Try decoding
        try:
            base64.b85decode(total_str)
            print("Valid Base85")
        except Exception as e:
            print(f"Base85 decode failed: {e}")
            
        try:
            if "," in total_str:
                actual_b64 = total_str.split(",", 1)[1]
            else:
                actual_b64 = total_str
            base64.b64decode(actual_b64)
            print("Valid Base64 (after prefix removal if any)")
        except Exception as e:
            print(f"Base64 decode failed: {e}")
