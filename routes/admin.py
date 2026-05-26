from fastapi import APIRouter, File, UploadFile, Form, Depends, HTTPException
from typing import List
from model import House, JardKidzPlan
from fastapi.responses import JSONResponse
from starlette import status
from utill import get_token, chunk_base64_string, reassemble_base64_string, get_image_url, upload_to_cloudinary, delete_from_cloudinary

import logging
import os
import secrets
import json
import datetime
from db.database import house_collection, jard_kidz_collection, user_collection, transactions_collection, products_collection, orders_collection, reviews_collection

logger = logging.getLogger("jardx")

def verify_admin_token(payload: dict = Depends(get_token)):
    """
    Decodes the JWT token and verifies that the logged-in email is strictly the admin email.
    """
    try:
        user_id = payload.get("id")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: User ID missing"
            )
        
        # Look up user in database
        user = user_collection.find_one({"_id": user_id})
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Administrator account not found"
            )
        
        email = user.get("email", "").lower()
        admin_email = "jarvadgroup.business@gmail.com".lower()
        
        if email != admin_email:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access Denied: You do not have administrator privileges"
            )
            
        return user
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error in verify_admin_token: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed"
        )

router = APIRouter(
    prefix="/admin", 
    tags=["Admin"],
    dependencies=[Depends(verify_admin_token)]
)

def save_house_image(file: UploadFile):
    file.file.seek(0, os.SEEK_END)
    file_size = file.file.tell()
    file.file.seek(0)
    
    if file_size > 1024 * 1024: # Allowing up to 1MB now since Cloudinary handles it well
        return None, "File size exceeds 1MB limit."
    
    # Read file contents
    contents = file.file.read()
    
    # Upload to Cloudinary
    url = upload_to_cloudinary(contents, folder="houses")
    
    if not url:
        return None, "Upload to Cloudinary failed."
    
    return url, None

@router.post("/add-house")
async def add_house(
    house_name: str = Form(...),
    house_about: str = Form(...),
    house_location: str = Form(...),
    house_status: str = Form(...),
    house_pricing_plan: str = Form(...), # JSON string
    house_landmarks: str = Form("[]"), # JSON string
    house_benefits: str = Form("[]"), # JSON string
    images: list[UploadFile] = File(...)
):
    try:
        new_image_urls = []
        for img in images:
            url, error = save_house_image(img)
            if error:
                return JSONResponse({"message": error, "status": 400})
            new_image_urls.append(url)

        data = {
            "house_name": house_name,
            "house_about": house_about,
            "house_location": house_location,
            "house_status": house_status,
            "house_pricing_plan": json.loads(house_pricing_plan),
            "house_landmarks": json.loads(house_landmarks),
            "house_benefits": json.loads(house_benefits),
            "house_image": new_image_urls
        }
        
        logger.info(f"Adding house: {house_name}")
        house_collection.insert_one(data)   

        # Notify users who have enabled push notifications and store in-app notifications
        try:
            from utill import send_push_notification_to_user, get_image_url
            from db.database import notifications_collection
            import datetime
            
            first_image = get_image_url(new_image_urls[0]) if new_image_urls else ""
            all_users = list(user_collection.find({}))
            
            # Save in-app notification records for all users so it displays on their screen
            in_app_notifs = []
            for u in all_users:
                u_id = str(u["_id"])
                in_app_notifs.append({
                    "user_id": u_id,
                    "title": "New Real Estate Listed! 🏡",
                    "body": f"A premium property '{house_name}' is now available in {house_location}.",
                    "type": "ESTATE",
                    "action_text": "View",
                    "created_at": datetime.datetime.utcnow().isoformat(),
                    "is_read": False,
                    "image": first_image
                })
            if in_app_notifs:
                notifications_collection.insert_many(in_app_notifs)
                
            # Deliver physical push notification to eligible devices
            for u in all_users:
                if u.get("push_token") and (u.get("notification_settings") or {}).get("push", False):
                    send_push_notification_to_user(
                        user_doc=u,
                        title="New Real Estate Listed! 🏡",
                        body=f"A premium property '{house_name}' is now available in {house_location}.",
                        data_payload={"screen": "notifications", "image": first_image}
                    )
        except Exception as push_err:
            logger.error(f"Failed to send add-house push notifications: {push_err}")

        return JSONResponse({"message": "House added successfully", "status": status.HTTP_200_OK})
    except Exception as e:
        logger.error(f"Error adding house: {e}")
        return JSONResponse({"message": f"Error adding house: {str(e)}", "status": 500})



@router.get("/get-house",response_model=list[House])
async def get_house():
    all_house_data=list(house_collection.find({}))
    data = []
    for house in all_house_data:
        house["_id"] = str(house["_id"])
        new_images = []
        for img in house.get("house_image", []):
            if isinstance(img, list):
                # Still legacy chunked data
                new_images.append(reassemble_base64_string(img))
            else:
                # URL (migrated or new)
                new_images.append(get_image_url(img))
        house["house_image"] = new_images
        data.append(house)
    
    logger.info("House list fetched")
    return JSONResponse({"message":"House data","data":data,"status":status.HTTP_200_OK})




@router.delete("/delete-house/{house_id}")
async def delete_house(house_id:str):
    # Find house first to get image URLs
    house = house_collection.find_one({"_id": house_id})
    if house and house.get("house_image"):
        for img_url in house["house_image"]:
            if "cloudinary" in img_url:
                delete_from_cloudinary(img_url)
    
    house_collection.delete_one({"_id":house_id})
    return JSONResponse({"message":"House deleted successfully","status":status.HTTP_200_OK})


@router.patch("/update-house-status/{house_id}")
async def update_house_status(house_id:str,data:dict):
    logger.info(f"Updating house status: {house_id} to {data.get('house_status')}")
    house_collection.update_one({"_id":house_id},{"$set":{"house_status":data["house_status"]}})
    return JSONResponse({"message":"House status updated successfully","status":status.HTTP_200_OK})


@router.put("/update-house/{house_id}")
async def update_house(
    house_id: str,
    house_name: str = Form(None),
    house_about: str = Form(None),
    house_location: str = Form(None),
    house_status: str = Form(None),
    house_pricing_plan: str = Form(None),
    house_landmarks: str = Form(None),
    house_benefits: str = Form(None),
    existing_images: str = Form("[]"), # JSON string of existing URLs to keep
    images: list[UploadFile] = File(None)
):
    try:
        update_data = {}
        if house_name: update_data["house_name"] = house_name
        if house_about: update_data["house_about"] = house_about
        if house_location: update_data["house_location"] = house_location
        if house_status: update_data["house_status"] = house_status
        if house_pricing_plan: update_data["house_pricing_plan"] = json.loads(house_pricing_plan)
        if house_landmarks: update_data["house_landmarks"] = json.loads(house_landmarks)
        if house_benefits: update_data["house_benefits"] = json.loads(house_benefits)

        # Handle images
        final_images = json.loads(existing_images)
        
        # Strip domain from existing images if present
        cleaned_existing = []
        for img in final_images:
            if "/uploads/" in img:
                rel_path = img.split("/uploads/")[1]
                cleaned_existing.append(f"/uploads/{rel_path}")
            else:
                cleaned_existing.append(img)
        final_images = cleaned_existing

        # Identify images being removed from the existing list
        if house_name: # Just a check to see if we have house data
            house = house_collection.find_one({"_id": house_id})
            if house and house.get("house_image"):
                for old_img in house["house_image"]:
                    if old_img not in final_images and "cloudinary" in old_img:
                        # This image was removed from the list, delete it from cloud
                        delete_from_cloudinary(old_img)

        if images:
            for img in images:
                url, error = save_house_image(img)
                if error:
                    return JSONResponse({"message": error, "status": 400})
                final_images.append(url)
        
        if final_images or images: # Only update if images were touched
            update_data["house_image"] = final_images

        house_collection.update_one({"_id": house_id}, {"$set": update_data})

        logger.info(f"House updated: {house_id}")
        return JSONResponse({"message": "House updated successfully", "status": status.HTTP_200_OK})
    except Exception as e:
        logger.error(f"Error updating house {house_id}: {e}")
        return JSONResponse({"message": f"Error updating house: {str(e)}", "status": 500})



@router.get("/get-selected-house-by-id/{house_id}")
async def get_selected_house_by_id(house_id:str):
    house_data=house_collection.find_one({"_id":house_id})
    if not house_data:
        return JSONResponse({"message": "House not found", "status": 404})
        
    new_image_collection=[]
    for i in house_data.get("house_image", []):
        if isinstance(i, list):
            reassembled_data=reassemble_base64_string(i)
            new_image_collection.append(reassembled_data)
        else:
            new_image_collection.append(get_image_url(i))
            
    house_data["house_image"]=new_image_collection
    house_data["_id"] = str(house_data["_id"])
    logger.info(f"Selected house fetched: {house_id}")
    return JSONResponse({"message":"House data","data":house_data,"status":status.HTTP_200_OK})



@router.get("/get-child-investments")
async def get_child_investments():
    all_investments = list(jard_kidz_collection.find({}))
    logger.info(f"Fetched {len(all_investments)} child investments")
    return JSONResponse({"message": "Child investments fetched", "data": all_investments, "status": status.HTTP_200_OK})


# --- USER MANAGEMENT ENDPOINTS ---

@router.get("/users")
async def get_all_users():
    try:
        # Fetch all users, excluding sensitive fields
        projection = {
            "password": 0,
            "transaction_pin": 0,
            "otp": 0
        }
        all_users = list(user_collection.find({}, projection=projection))
        
        # Clean data for JSON (Astra IDs)
        cleaned_users = []
        for u in all_users:
            u_clean = dict(u)
            u_clean["_id"] = str(u["_id"])
            cleaned_users.append(u_clean)
            
        logger.info(f"Admin fetched {len(cleaned_users)} users")
        return JSONResponse({"message": "Users fetched successfully", "data": cleaned_users, "status": 200})
    except Exception as e:
        logger.error(f"Error fetching users: {e}")
        return JSONResponse({"message": str(e), "status": 500})

@router.put("/update-user/{user_id}")
async def update_user(user_id: str, data: dict):
    try:
        # Update name and email
        update_data = {
            "user_name": data.get("user_name"),
            "email": data.get("email"),
            "wallet_balance": float(data.get("wallet_balance", 0))
        }
        # Remove None values
        update_data = {k: v for k, v in update_data.items() if v is not None}
        
        user_collection.update_one({"_id": user_id}, {"$set": update_data})
        logger.info(f"Admin updated user {user_id}")
        return JSONResponse({"message": "User updated successfully", "status": 200})
    except Exception as e:
        logger.error(f"Error updating user {user_id}: {e}")
        return JSONResponse({"message": str(e), "status": 500})

@router.patch("/toggle-user-block/{user_id}")
async def toggle_user_block(user_id: str, data: dict):
    try:
        new_status = data.get("status") # 'Active' or 'Blocked'
        user_collection.update_one({"_id": user_id}, {"$set": {"status": new_status}})
        logger.info(f"Admin changed user status for {user_id} to {new_status}")
        return JSONResponse({"message": f"User status changed to {new_status}", "status": 200})
    except Exception as e:
        logger.error(f"Error toggling user block for {user_id}: {e}")
        return JSONResponse({"message": str(e), "status": 500})

@router.delete("/delete-user/{user_id}")
async def delete_user(user_id: str):
    try:
        user_collection.delete_one({"_id": user_id})
        logger.info(f"Admin deleted user {user_id}")
        return JSONResponse({"message": "User deleted successfully", "status": 200})
    except Exception as e:
        logger.error(f"Error deleting user {user_id}: {e}")
        return JSONResponse({"message": str(e), "status": 500})

# --- TRANSACTION MANAGEMENT ENDPOINTS ---

@router.get("/transactions")
async def get_all_transactions():
    try:
        all_tx = list(transactions_collection.find({}).sort({"created_at": -1}))
        
        # Clean IDs for JSON
        for tx in all_tx:
            tx["_id"] = str(tx["_id"])
            
            # Fetch user info if needed for the admin view
            user = user_collection.find_one({"_id": tx["user_id"]}, projection={"user_name": 1, "email": 1})
            if user:
                tx["user_info"] = {
                    "name": user.get("user_name"),
                    "email": user.get("email")
                }
            
        logger.info(f"Admin fetched {len(all_tx)} transactions")
        return JSONResponse({"message": "Transactions fetched successfully", "data": all_tx, "status": 200})
    except Exception as e:
        logger.error(f"Error fetching transactions: {e}")
        return JSONResponse({"message": str(e), "status": 500})

@router.post("/approve-transaction/{tx_ref}")
async def approve_transaction(tx_ref: str):
    try:
        # 1. Find transaction
        transaction = transactions_collection.find_one({"tx_ref": tx_ref})
        if not transaction:
            return JSONResponse({"message": "Transaction not found", "status": 404})
        
        if transaction["status"] == "SUCCESS":
            return JSONResponse({"message": "Transaction already approved", "status": 400})

        # 2. Update status
        transactions_collection.update_one(
            {"tx_ref": tx_ref},
            {"$set": {"status": "SUCCESS", "completed_at": secrets.token_hex(4)}} # placeholder for timestamp
        )
        # Using real timestamp
        import datetime
        transactions_collection.update_one(
            {"tx_ref": tx_ref},
            {"$set": {"completed_at": datetime.datetime.utcnow().isoformat()}}
        )

        # 3. Credit user wallet
        user_id = transaction["user_id"]
        amount = float(transaction["amount"])
        
        user = user_collection.find_one({"_id": user_id})
        if user:
            new_balance = float(user.get("wallet_balance", 0)) + amount
            user_collection.update_one({"_id": user_id}, {"$set": {"wallet_balance": new_balance}})
            
            # 4. Trigger referral logic if applicable
            from utill import process_referral_logic
            process_referral_logic(user_id, amount, user_collection, transactions_collection)
            
            logger.info(f"Admin APPROVED transaction {tx_ref} | Credited {amount} to {user_id}")
            return JSONResponse({"message": "Transaction approved and wallet credited", "status": 200})
        else:
            return JSONResponse({"message": "User not found", "status": 404})

    except Exception as e:
        logger.error(f"Error approving transaction {tx_ref}: {e}")
        return JSONResponse({"message": str(e), "status": 500})

@router.post("/reject-transaction/{tx_ref}")
async def reject_transaction(tx_ref: str):
    try:
        transaction = transactions_collection.find_one({"tx_ref": tx_ref})
        if not transaction:
            return JSONResponse({"message": "Transaction not found", "status": 404})
        
        import datetime
        transactions_collection.update_one(
            {"tx_ref": tx_ref},
            {"$set": {
                "status": "FAILED", 
                "completed_at": datetime.datetime.utcnow().isoformat(),
                "admin_note": "Rejected by administrator"
            }}
        )
        
        logger.info(f"Admin REJECTED transaction {tx_ref}")
        return JSONResponse({"message": "Transaction rejected", "status": 200})
    except Exception as e:
        logger.error(f"Error rejecting transaction {tx_ref}: {e}")
        return JSONResponse({"message": str(e), "status": 500})

# --- JARDPROC MANAGEMENT ---

def save_product_image(file: UploadFile):
    contents = file.file.read()
    url = upload_to_cloudinary(contents, folder="products")
    return url

@router.post("/add-product")
async def add_product(
    name: str = Form(...),
    description: str = Form(...),
    price: float = Form(...),
    category: str = Form(...),
    stock: int = Form(...),
    images: List[UploadFile] = File(...),
    volume_value: str = Form(None),
    volume_unit: str = Form(None),
    variants: str = Form(None)
):
    try:
        image_urls = []
        for img in images:
            url = save_product_image(img)
            image_urls.append(url)
            
        volume_val = None
        if volume_value and volume_value.strip():
            try:
                volume_val = float(volume_value)
            except ValueError:
                pass
            
        data = {
            "name": name,
            "description": description,
            "price": price,
            "category": category,
            "stock": stock,
            "image": image_urls, # Storing as a list
            "status": "In Stock" if stock > 0 else "Out of Stock",
            "volume_value": volume_val,
            "volume_unit": volume_unit if volume_unit else None,
            "variants": json.loads(variants) if variants and variants.strip() else [],
            "created_at": datetime.datetime.utcnow().isoformat()
        }
        from db.database import products_collection
        products_collection.insert_one(data)

        # Notify users who have enabled push notifications and store in-app notifications
        try:
            from utill import send_push_notification_to_user, get_image_url
            from db.database import notifications_collection
            import datetime
            
            first_image = get_image_url(image_urls[0]) if image_urls else ""
            all_users = list(user_collection.find({}))
            
            # Save in-app notification records for all users so it displays on their screen
            in_app_notifs = []
            for u in all_users:
                u_id = str(u["_id"])
                in_app_notifs.append({
                    "user_id": u_id,
                    "title": "New Product Alert! 🛍️",
                    "body": f"We just added '{name}' to our store. Check it out now!",
                    "type": "PRODUCT",
                    "action_text": "View",
                    "created_at": datetime.datetime.utcnow().isoformat(),
                    "is_read": False,
                    "image": first_image
                })
            if in_app_notifs:
                notifications_collection.insert_many(in_app_notifs)
                
            # Deliver physical push notification to eligible devices
            for u in all_users:
                if u.get("push_token") and (u.get("notification_settings") or {}).get("push", False):
                    send_push_notification_to_user(
                        user_doc=u,
                        title="New Product Alert! 🛍️",
                        body=f"We just added '{name}' to our store. Check it out now!",
                        data_payload={"screen": "notifications", "image": first_image}
                    )
        except Exception as push_err:
            logger.error(f"Failed to send add-product push notifications: {push_err}")

        return JSONResponse({"message": "Product added successfully", "status": 200})
    except Exception as e:
        return JSONResponse({"message": str(e), "status": 500})

@router.get("/get-all-orders")
async def get_all_orders():
    try:
        from db.database import orders_collection
        orders = list(orders_collection.find({}).sort({"created_at": -1}))
        for o in orders:
            o["_id"] = str(o["_id"])
            # Fetch user info
            user = user_collection.find_one({"_id": o["user_id"]}, projection={"user_name": 1, "email": 1})
            if user:
                o["user_info"] = user
        return JSONResponse({"message": "Orders fetched", "data": orders, "status": 200})
    except Exception as e:
        return JSONResponse({"message": str(e), "status": 500})

@router.patch("/update-order-status/{order_id}")
async def update_order_status(order_id: str, data: dict):
    try:
        from db.database import orders_collection
        new_status = data.get("status")
        orders_collection.update_one({"order_id": order_id}, {"$set": {"status": new_status}})
        
        # Trigger notification logic here later
        logger.info(f"Admin updated order {order_id} status to {new_status}")
        return JSONResponse({"message": "Order status updated", "status": 200})
    except Exception as e:
        return JSONResponse({"message": str(e), "status": 500})

@router.post("/bulk-upload-products")
async def bulk_upload_products(file: UploadFile = File(...)):
    try:
        import csv
        import io
        from db.database import products_collection
        
        content = await file.read()
        decode_content = content.decode('utf-8')
        f = io.StringIO(decode_content)
        reader = csv.DictReader(f)
        
        products = []
        for row in reader:
            products.append({
                "name": row["name"],
                "description": row["description"],
                "price": float(row["price"]),
                "category": row["category"],
                "stock": int(row["stock"]),
                "image": row.get("image", ""), # Can be a placeholder URL
                "status": "In Stock" if int(row["stock"]) > 0 else "Out of Stock",
                "created_at": datetime.datetime.utcnow().isoformat()
            })
            
        if products:
            products_collection.insert_many(products)
            
        return JSONResponse({"message": f"Successfully uploaded {len(products)} products", "status": 200})
    except Exception as e:
        return JSONResponse({"message": str(e), "status": 500})

@router.put("/update-product/{product_id}")
async def update_product(
    product_id: str,
    name: str = Form(None),
    description: str = Form(None),
    price: float = Form(None),
    category: str = Form(None),
    stock: int = Form(None),
    existing_images: str = Form(None),
    images: List[UploadFile] = File(None),
    volume_value: str = Form(None),
    volume_unit: str = Form(None),
    variants: str = Form(None)
):
    try:
        # Fetch the product from db to get current name and image details
        from db.database import products_collection
        product = products_collection.find_one({"_id": product_id})
        if not product:
            return JSONResponse({"message": "Product not found", "status": 404})
            
        prod_name = name if name else product.get("name", "Product")
        
        update_data = {}
        if name: update_data["name"] = name
        if description: update_data["description"] = description
        if price is not None: update_data["price"] = price
        if category: update_data["category"] = category
        if stock is not None: 
            update_data["stock"] = stock
            update_data["status"] = "In Stock" if stock > 0 else "Out of Stock"
            
        if volume_value is not None:
            if volume_value.strip():
                try:
                    update_data["volume_value"] = float(volume_value)
                except ValueError:
                    update_data["volume_value"] = None
            else:
                update_data["volume_value"] = None
                
        if volume_unit is not None:
            update_data["volume_unit"] = volume_unit if volume_unit.strip() else None
            
        if variants is not None:
            update_data["variants"] = json.loads(variants) if variants.strip() else []
        
        # Handle images
        final_images = []
        if existing_images:
            final_images = json.loads(existing_images)
            
        if images:
            for img in images:
                url = save_product_image(img)
                final_images.append(url)
        
        if final_images:
            update_data["image"] = final_images
            
        products_collection.update_one({"_id": product_id}, {"$set": update_data})
        
        # Determine the first image URL to attach to notifications
        first_image = ""
        if final_images:
            first_image = final_images[0]
        elif product.get("image"):
            first_image = product["image"][0] if isinstance(product["image"], list) else product["image"]
            
        # Notify users of the product update and save to notifications_collection
        try:
            from utill import send_push_notification_to_user, get_image_url
            from db.database import user_collection, notifications_collection
            import datetime
            
            resolved_image = get_image_url(first_image) if first_image else ""
            all_users = list(user_collection.find({}))
            
            # Save in-app notification records for all users so it displays on their screen
            in_app_notifs = []
            for u in all_users:
                u_id = str(u["_id"])
                in_app_notifs.append({
                    "user_id": u_id,
                    "title": "Product Updated! 🛍️",
                    "body": f"The product '{prod_name}' has been updated with new details.",
                    "type": "PRODUCT",
                    "action_text": "View",
                    "created_at": datetime.datetime.utcnow().isoformat(),
                    "is_read": False,
                    "image": resolved_image
                })
            if in_app_notifs:
                notifications_collection.insert_many(in_app_notifs)
                
            # Deliver physical push notification to eligible devices
            for u in all_users:
                if u.get("push_token") and (u.get("notification_settings") or {}).get("push", False):
                    send_push_notification_to_user(
                        user_doc=u,
                        title="Product Updated! 🛍️",
                        body=f"The product '{prod_name}' has been updated with new details.",
                        data_payload={"screen": "notifications", "image": resolved_image}
                    )
        except Exception as push_err:
            logger.error(f"Failed to send update-product push notifications: {push_err}")
            
        return JSONResponse({"message": "Product updated successfully", "status": 200})
    except Exception as e:
        return JSONResponse({"message": str(e), "status": 500})

@router.delete("/delete-product/{product_id}")
async def delete_product(product_id: str):
    try:
        from db.database import products_collection
        # Find product to delete image from cloudinary if needed
        product = products_collection.find_one({"_id": product_id})
        if product and "cloudinary" in product.get("image", ""):
            delete_from_cloudinary(product["image"])
            
        products_collection.delete_one({"_id": product_id})
        return JSONResponse({"message": "Product deleted successfully", "status": 200})
    except Exception as e:
        return JSONResponse({"message": str(e), "status": 500})

# --- PARTNER & VENDOR MANAGEMENT ---

@router.get("/partners-list")
async def get_partners_list(type: str = "partner"):
    try:
        from db.database import vendors_collection, partners_collection, user_collection
        # type can be 'vendor' or 'partner'
        if type == 'vendor':
            data = list(vendors_collection.find({}))
            logger.info(f"Admin fetched {len(data)} vendors")
        else:
            data = list(partners_collection.find({}))
            logger.info(f"Admin fetched {len(data)} partners")
            
        for item in data:
            item["_id"] = str(item["_id"])
            # Fetch user info for display if user_id exists
            user_id = item.get("user_id")
            if user_id:
                user = user_collection.find_one({"_id": user_id}, projection={"user_name": 1, "email": 1, "phone": 1})
                if user:
                    item["user_info"] = user
                
        return JSONResponse({"message": "Data fetched", "data": data, "status": 200})
    except Exception as e:
        logger.error(f"Error fetching partners-list: {e}")
        return JSONResponse({"message": str(e), "status": 500})

@router.patch("/verify-account/{account_id}")
async def verify_account(account_id: str, data: dict):
    try:
        from db.database import vendors_collection, partners_collection
        acc_type = data.get("type") # 'vendor' or 'partner'
        new_status = data.get("status") # 'verified', 'rejected', 'unverified'
        
        collection = vendors_collection if acc_type == 'vendor' else partners_collection
        collection.update_one({"_id": account_id}, {"$set": {"status": new_status}})
        
        return JSONResponse({"message": f"Account {new_status} successfully", "status": 200})
    except Exception as e:
        return JSONResponse({"message": str(e), "status": 500})

@router.delete("/delete-account/{account_id}")
async def delete_account(account_id: str, type: str):
    try:
        from db.database import vendors_collection, partners_collection
        collection = vendors_collection if type == 'vendor' else partners_collection
        collection.delete_one({"_id": account_id})
        return JSONResponse({"message": "Account record deleted", "status": 200})
    except Exception as e:
        return JSONResponse({"message": str(e), "status": 500})

@router.get("/get-products")
async def admin_get_products(category: str = "All"):
    try:
        from db.database import products_collection
        query = {}
        if category != "All":
            query = {"category": category}
            
        products = list(products_collection.find(query))
        for p in products:
            p["_id"] = str(p["_id"])
            if isinstance(p.get("image"), list):
                p["image"] = [get_image_url(img) for img in p["image"]]
            else:
                p["image"] = get_image_url(p.get("image"))
        return JSONResponse({"message": "Products fetched", "data": products, "status": 200})
    except Exception as e:
        return JSONResponse({"message": str(e), "status": 500})
