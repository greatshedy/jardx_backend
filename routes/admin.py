from fastapi import APIRouter, File, UploadFile, Form
from model import House, JardKidzPlan
from db.database import house_collection, jard_kidz_collection, user_collection, transactions_collection
from fastapi.responses import JSONResponse
from starlette import status
from utill import chunk_base64_string,reassemble_base64_string, get_image_url

import logging
import os
import secrets
import json


logger = logging.getLogger("jardx")

router = APIRouter(prefix="/admin", tags=["Admin"]) 

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads", "houses")

def save_house_image(file: UploadFile):
    # Check file size (200KB = 200 * 1024 bytes)
    file.file.seek(0, os.SEEK_END)
    file_size = file.file.tell()
    file.file.seek(0)
    
    if file_size > 200 * 1024:
        return None, "File size exceeds 200KB limit."
    
    # Generate unique filename
    filename = f"{secrets.token_hex(8)}{os.path.splitext(file.filename)[1] or '.jpg'}"
    filepath = os.path.join(UPLOAD_DIR, filename)
    
    with open(filepath, "wb") as buffer:
        buffer.write(file.file.read())
    
    return f"/uploads/houses/{filename}", None

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

@router.delete("/delete-transaction/{tx_ref}")
async def delete_transaction(tx_ref: str):
    try:
        transactions_collection.delete_one({"tx_ref": tx_ref})
        logger.info(f"Admin DELETED transaction {tx_ref}")
        return JSONResponse({"message": "Transaction deleted successfully", "status": 200})
    except Exception as e:
        logger.error(f"Error deleting transaction {tx_ref}: {e}")
        return JSONResponse({"message": str(e), "status": 500})
