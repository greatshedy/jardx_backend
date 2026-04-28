from fastapi import APIRouter
from model import House, JardKidzPlan
from db.database import house_collection, jard_kidz_collection, user_collection
from fastapi.responses import JSONResponse
from starlette import status
from utill import chunk_base64_string,reassemble_base64_string
import logging

logger = logging.getLogger("jardx")

router = APIRouter(prefix="/admin", tags=["Admin"]) 

@router.post("/add-house")
async def add_house(house_data:House):
    data=dict(house_data)
    logger.debug(f"Adding house: {data.get('house_name')}")
    new_chunked_data=[]
    for i in data["house_image"]:
        chunked_data=chunk_base64_string(i)
        new_chunked_data.append(chunked_data)

    data["house_image"]=new_chunked_data
    logger.info(f"House data processed for: {data.get('house_name')}")
    house_collection.insert_one(data)   
    return JSONResponse({"message":"House added successfully","status":status.HTTP_200_OK})


@router.get("/get-house",response_model=list[House])
async def get_house():
    all_house_data=house_collection.find({},projection={"house_image":0}).to_list()
    data=all_house_data
    new_image_collection=[]
    # for i in all_house_data:
    #     del i["house_image"]
    #     data.append(i)
        # for j in i["house_image"]:
        #     reassembled_data=reassemble_base64_string(j)
        #     new_image_collection.append(reassembled_data)
        # i["house_image"]=new_image_collection
        # data.append(i)
    
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
async def update_house(house_id:str,house_data:House):
    new_chunked_data=[]
    house_data=dict(house_data)
    for i in house_data["house_image"]:
        chunked_data=chunk_base64_string(i)
        new_chunked_data.append(chunked_data)

    house_data["house_image"]=new_chunked_data
    house_collection.update_one({"_id":house_id},{"$set":dict(house_data)})
    return JSONResponse({"message":"House updated successfully","status":status.HTTP_200_OK})


@router.put("/get-selected-house-by-id/{house_id}")
async def get_selected_house_by_id(house_id:str):
    house_data=house_collection.find_one({"_id":house_id})
    new_image_collection=[]
    for i in house_data["house_image"]:
        reassembled_data=reassemble_base64_string(i)
        new_image_collection.append(reassembled_data)
    house_data["house_image"]=new_image_collection
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
