from fastapi import APIRouter, File, UploadFile, Form, Depends, HTTPException, Query
from typing import List
from model import House, JardKidzPlan
from fastapi.responses import JSONResponse, FileResponse
from starlette import status
from utill import get_token, chunk_base64_string, reassemble_base64_string, get_image_url, upload_to_cloudinary, delete_from_cloudinary

import logging
import os
import secrets
import json
import datetime
from db.database import db, db_aux, house_collection, jard_kidz_collection, user_collection, transactions_collection, products_collection, orders_collection, reviews_collection, sub_settings_collection, vendors_collection, partners_collection, notifications_collection, reports_collection, deletion_requests_collection, admin_push_subscriptions_collection, housing_subscriptions_collection, portfolio_collection

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
        admin_emails = [e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "jarvadgroup.business@gmail.com").split(",")]
        
        if email not in admin_emails:
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

def _safe_json(value, fallback):
    try:
        if value in (None, ""):
            return fallback
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _optional_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
    house_type: str = Form(""),
    house_is_promo: bool = Form(False),
    house_promo_type: str = Form(""),
    house_promo_value: float = Form(0.0),
    land_enabled: bool = Form(True),
    housing_enabled: bool = Form(False),
    property_type_prices: str = Form("{}"),
    housing_unit_types: str = Form("[]"),
    housing_registration_fee: str = Form(None),
    housing_savings_multiplier: str = Form(None),
    promo_visible: bool = Form(True),
    promo_starts_at: str = Form(""),
    promo_ends_at: str = Form(""),
    promo_label: str = Form(""),
    promo_terms: str = Form(""),
    promo_applies_to: str = Form("unit_price"),
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
            "house_type": house_type,
            "house_is_promo": house_is_promo,
            "house_promo_type": house_promo_type,
            "house_promo_value": house_promo_value,
            "land_enabled": land_enabled,
            "housing_enabled": housing_enabled,
            "property_type_prices": _safe_json(property_type_prices, {}),
            "housing_unit_types": _safe_json(housing_unit_types, []),
            "housing_registration_fee": _optional_float(housing_registration_fee),
            "housing_savings_multiplier": _optional_float(housing_savings_multiplier),
            "promo_visible": promo_visible,
            "promo_starts_at": promo_starts_at or "",
            "promo_ends_at": promo_ends_at or "",
            "promo_label": promo_label or "",
            "promo_terms": promo_terms or "",
            "promo_applies_to": promo_applies_to or "unit_price",
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


@router.get("/bulk-estate-template")
async def bulk_estate_template():
    template_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "estate_template.csv")
    return FileResponse(template_path, media_type="text/csv", filename="estate_template.csv")


@router.post("/bulk-add-houses")
async def bulk_add_houses(payload: dict):
    try:
        houses = payload.get("houses", [])
        if not houses:
            return JSONResponse({"message": "No houses provided", "status": 400})
        created = 0
        errors = []
        for i, house_data in enumerate(houses):
            try:
                pricing = house_data.get("house_pricing_plan", [])
                if isinstance(pricing, str):
                    pricing = json.loads(pricing)
                landmarks = house_data.get("house_landmarks", [])
                if isinstance(landmarks, str):
                    landmarks = [l.strip() for l in landmarks.split("|") if l.strip()]
                benefits = house_data.get("house_benefits", [])
                if isinstance(benefits, str):
                    benefits = [b.strip() for b in benefits.split("|") if b.strip()]
                doc = {
                    "house_name": house_data.get("house_name", ""),
                    "house_about": house_data.get("house_about", ""),
                    "house_location": house_data.get("house_location", ""),
                    "house_status": house_data.get("house_status", "Active"),
                    "house_type": house_data.get("house_type", ""),
                    "house_landmarks": landmarks,
                    "house_benefits": benefits,
                    "house_pricing_plan": pricing,
                    "house_image": [],
                    "house_is_promo": house_data.get("house_is_promo", False),
                    "house_promo_type": house_data.get("house_promo_type", ""),
                    "house_promo_value": float(house_data.get("house_promo_value", 0)),
                }
                house_collection.insert_one(doc)
                created += 1
            except Exception as row_err:
                errors.append({"row": i + 1, "name": house_data.get("house_name", f"Row {i + 1}"), "error": str(row_err)})
        return JSONResponse({
            "message": f"{created} houses created",
            "data": {"created": created, "errors": errors},
            "status": 200
        })
    except Exception as e:
        logger.error(f"Error in bulk-add-houses: {e}")
        return JSONResponse({"message": str(e), "status": 500})


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
    house_type: str = Form(None),
    house_is_promo: bool = Form(None),
    house_promo_type: str = Form(None),
    house_promo_value: float = Form(None),
    land_enabled: bool = Form(None),
    housing_enabled: bool = Form(None),
    property_type_prices: str = Form(None),
    housing_unit_types: str = Form(None),
    housing_registration_fee: str = Form(None),
    housing_savings_multiplier: str = Form(None),
    promo_visible: bool = Form(None),
    promo_starts_at: str = Form(None),
    promo_ends_at: str = Form(None),
    promo_label: str = Form(None),
    promo_terms: str = Form(None),
    promo_applies_to: str = Form(None),
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
        if house_type is not None: update_data["house_type"] = house_type
        if house_is_promo is not None: update_data["house_is_promo"] = house_is_promo
        if house_promo_type is not None: update_data["house_promo_type"] = house_promo_type
        if house_promo_value is not None: update_data["house_promo_value"] = house_promo_value
        if land_enabled is not None: update_data["land_enabled"] = land_enabled
        if housing_enabled is not None: update_data["housing_enabled"] = housing_enabled
        if property_type_prices is not None: update_data["property_type_prices"] = _safe_json(property_type_prices, {})
        if housing_unit_types is not None: update_data["housing_unit_types"] = _safe_json(housing_unit_types, [])
        if housing_registration_fee is not None: update_data["housing_registration_fee"] = _optional_float(housing_registration_fee)
        if housing_savings_multiplier is not None: update_data["housing_savings_multiplier"] = _optional_float(housing_savings_multiplier)
        if promo_visible is not None: update_data["promo_visible"] = promo_visible
        if promo_starts_at is not None: update_data["promo_starts_at"] = promo_starts_at
        if promo_ends_at is not None: update_data["promo_ends_at"] = promo_ends_at
        if promo_label is not None: update_data["promo_label"] = promo_label
        if promo_terms is not None: update_data["promo_terms"] = promo_terms
        if promo_applies_to is not None: update_data["promo_applies_to"] = promo_applies_to

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
async def get_all_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str = Query(""),
    status_filter: str = Query("ALL", alias="status"),
):
    try:
        # Fetch users, excluding sensitive fields
        projection = {
            "password": 0,
            "transaction_pin": 0,
            "otp": 0
        }

        query = {}
        if status_filter and status_filter.upper() != "ALL":
            query["status"] = status_filter

        search = (search or "").strip().lower()

        if search:
            # Substring search across name/email (Data API has no regex), then paginate in Python
            all_users = list(user_collection.find(query, projection=projection))
            matched = [
                u for u in all_users
                if search in (u.get("user_name", "") or "").lower()
                or search in (u.get("email", "") or "").lower()
            ]
            total_count = len(matched)
            total_pages = max(1, (total_count + page_size - 1) // page_size)
            start = (page - 1) * page_size
            page_users = matched[start:start + page_size]
        else:
            try:
                total_count = user_collection.count_documents(query, upper_bound=1000000)
            except Exception:
                total_count = len(list(user_collection.find(query, projection={"_id": 1})))
            total_pages = max(1, (total_count + page_size - 1) // page_size)
            page_users = list(user_collection.find(
                query,
                sort={"_id": 1},
                limit=page_size,
                skip=(page - 1) * page_size,
                projection=projection,
            ))

        cleaned_users = []
        for u in page_users:
            u_clean = dict(u)
            u_clean["_id"] = str(u["_id"])
            cleaned_users.append(u_clean)

        logger.info(f"Admin fetched users page {page} ({len(cleaned_users)} of {total_count})")
        return JSONResponse({
            "message": "Users fetched successfully",
            "data": cleaned_users,
            "pagination": {
                "total_count": total_count,
                "total_pages": total_pages,
                "current_page": page,
                "page_size": page_size,
            },
            "status": 200,
        })
    except Exception as e:
        logger.error(f"Error fetching users: {e}")
        return JSONResponse({"message": str(e), "status": 500})


@router.get("/users/{user_id}/referrals")
async def get_user_referrals(user_id: str):
    """Users referred by the given user, plus their total referral earnings."""
    try:
        projection = {"password": 0, "transaction_pin": 0, "otp": 0}
        referred = list(user_collection.find({"referred_by": user_id}, projection=projection))

        cleaned = []
        for u in referred:
            uc = dict(u)
            uc["_id"] = str(u["_id"])
            cleaned.append(uc)

        earnings = 0.0
        try:
            credits = list(transactions_collection.find(
                {"user_id": user_id, "gateway": "Referral System"},
                projection={"amount": 1, "type": 1},
            ))
            earnings = sum(float(c.get("amount", 0) or 0) for c in credits if (c.get("type") == "CREDIT"))
        except Exception as earn_err:
            logger.error(f"Error computing referral earnings for {user_id}: {earn_err}")

        return JSONResponse({
            "message": "Referrals fetched",
            "data": cleaned,
            "count": len(cleaned),
            "earnings": earnings,
            "status": 200,
        })
    except Exception as e:
        logger.error(f"Error fetching referrals for {user_id}: {e}")
        return JSONResponse({"message": str(e), "status": 500})


# --- WEB PUSH (ADMIN BROWSER NOTIFICATIONS) ---

@router.get("/push/public-key")
async def push_public_key():
    return JSONResponse({"publicKey": os.getenv("VAPID_PUBLIC_KEY", ""), "status": 200})


@router.post("/push/subscribe")
async def push_subscribe(payload: dict, admin: dict = Depends(verify_admin_token)):
    try:
        sub = (payload or {}).get("subscription") or (payload or {})
        endpoint = sub.get("endpoint")
        keys = sub.get("keys") or {}
        if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
            return JSONResponse({"message": "Invalid push subscription", "status": 400})

        email = (admin.get("email") or "").lower()
        now = datetime.datetime.utcnow().isoformat()
        doc = {
            "endpoint": endpoint,
            "keys": {"p256dh": keys.get("p256dh"), "auth": keys.get("auth")},
            "admin_email": email,
            "user_agent": (payload or {}).get("user_agent", ""),
            "updated_at": now,
        }
        existing = admin_push_subscriptions_collection.find_one({"endpoint": endpoint})
        if existing:
            admin_push_subscriptions_collection.update_one({"_id": existing["_id"]}, {"$set": doc})
        else:
            doc["created_at"] = now
            admin_push_subscriptions_collection.insert_one(doc)
        logger.info(f"Admin push subscription saved for {email}")
        return JSONResponse({"message": "Subscribed", "status": 200})
    except Exception as e:
        logger.error(f"Error in /push/subscribe: {e}")
        return JSONResponse({"message": str(e), "status": 500})


@router.post("/push/unsubscribe")
async def push_unsubscribe(payload: dict):
    try:
        endpoint = (payload or {}).get("endpoint")
        if endpoint:
            admin_push_subscriptions_collection.delete_one({"endpoint": endpoint})
        return JSONResponse({"message": "Unsubscribed", "status": 200})
    except Exception as e:
        logger.error(f"Error in /push/unsubscribe: {e}")
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
        new_status = data.get("status")
        reason = data.get("reason", "")
        updates = {"status": new_status}
        if new_status == "Blocked" and reason:
            updates["block_reason"] = reason
        elif new_status == "Active":
            updates["block_reason"] = ""
        user_collection.update_one({"_id": user_id}, {"$set": updates})

        if new_status == "Blocked":
            notifications_collection.insert_one({
                "user_id": user_id,
                "title": "Account Restricted",
                "body": f"Your account has been restricted. Reason: {reason or 'No reason provided'}. Please contact customer care.",
                "type": "BLOCK",
                "is_read": False,
                "created_at": datetime.datetime.utcnow().isoformat()
            })

        logger.info(f"Admin changed user status for {user_id} to {new_status}")
        return JSONResponse({"message": f"User status changed to {new_status}", "status": 200})
    except Exception as e:
        logger.error(f"Error toggling user block for {user_id}: {e}")
        return JSONResponse({"message": str(e), "status": 500})

@router.get("/reports")
async def get_all_reports():
    try:
        reports = list(reports_collection.find({}).sort({"created_at": -1}))
        for r in reports:
            r["_id"] = str(r["_id"])
            reporter = user_collection.find_one({"_id": r.get("reporter_id")}, projection={"user_name": 1, "email": 1})
            r["reporter"] = {
                "name": reporter.get("user_name", "Unknown") if reporter else "Unknown",
                "email": reporter.get("email", "") if reporter else ""
            }
        return JSONResponse({"status": 200, "data": reports})
    except Exception as e:
        logger.error(f"Error in /reports: {e}")
        return JSONResponse({"message": str(e), "status": 500})

@router.post("/resolve-report/{report_id}")
async def resolve_report(report_id: str, data: dict):
    try:
        action = data.get("action", "resolved")
        reports_collection.update_one(
            {"_id": report_id},
            {"$set": {"status": action, "resolved_at": datetime.datetime.utcnow().isoformat()}}
        )
        return JSONResponse({"message": f"Report {action}", "status": 200})
    except Exception as e:
        logger.error(f"Error in /resolve-report: {e}")
        return JSONResponse({"message": str(e), "status": 500})

@router.post("/send-warning")
async def send_warning(payload: dict):
    try:
        user_id = payload.get("user_id")
        message = payload.get("message")
        if not user_id or not message:
            return JSONResponse({"message": "User ID and message are required", "status": 400})

        notifications_collection.insert_one({
            "user_id": user_id,
            "title": "Warning",
            "body": message,
            "type": "WARNING",
            "acknowledged": False,
            "is_read": False,
            "created_at": datetime.datetime.utcnow().isoformat()
        })

        return JSONResponse({"message": "Warning sent successfully", "status": 200})
    except Exception as e:
        logger.error(f"Error in /send-warning: {e}")
        return JSONResponse({"message": str(e), "status": 500})

def _collect_cloudinary_assets(user, partners, vendors, distributors):
    urls = set()
    if user.get("profile_pic"):
        urls.add(user.get("profile_pic"))
    for record in list(partners) + list(vendors):
        for key in ("photo", "certificate"):
            if record.get(key):
                urls.add(record.get(key))
        for image in record.get("gallery") or []:
            if image:
                urls.add(image)
    for record in distributors:
        if record.get("selfie"):
            urls.add(record.get("selfie"))
    return [url for url in urls if url and "cloudinary" in str(url)]


def _collect_user_related(user_id, email=""):
    data = {
        "portfolio": list(portfolio_collection.find({"user_id": user_id})),
        "transactions": list(transactions_collection.find({"user_id": user_id})),
        "orders": list(orders_collection.find({"user_id": user_id})),
        "reviews": list(reviews_collection.find({"user_id": user_id})),
        "partners": list(partners_collection.find({"user_id": user_id})),
        "vendors": list(vendors_collection.find({"user_id": user_id})),
        "jard_kidz": list(jard_kidz_collection.find({"user_id": user_id})),
        "notifications": list(notifications_collection.find({"user_id": user_id})),
        "housing_subscriptions": [
            item for item in housing_subscriptions_collection.find({})
            if str(item.get("user_id")) == str(user_id)
        ],
        "distributors": [
            item for item in db.get_collection("distributors").find({})
            if str(item.get("user_id")) == str(user_id)
        ],
    }

    aux_specs = {
        "deletion_requests": ("deletion_requests", ("user_id",)),
        "withdraw_requests": ("withdraw_requests", ("user_id",)),
        "kids_profiles": ("kids_profiles", ("userId",)),
        "land_purchases": ("land_purchases", ("userId",)),
        "land_payments": ("land_payments", ("userId",)),
        "land_referral_relationships": ("land_referral_relationships", ("referrerId", "referredUserId")),
        "referral_activities": ("referral_activities", ("referrerId", "intermediateReferrerId", "referredUserId")),
        "referral_commissions": ("referral_commissions", ("referrerId", "intermediateReferrerId", "investorId")),
        "reports": ("reports", ("reporter_id", "reported_user_id", "user_id", "vendor_id")),
    }
    for key, (collection_name, fields) in aux_specs.items():
        try:
            collection = db_aux.get_collection(collection_name)
            docs = list(collection.find({}))
        except Exception:
            docs = []
        data[key] = [
            doc for doc in docs
            if any(str(doc.get(field)) == str(user_id) for field in fields)
        ]

    support_messages = []
    if email:
        try:
            support_collection = db_aux.get_collection("support_messages")
            support_messages = [
                doc for doc in support_collection.find({})
                if str(doc.get("email", "")).lower() == str(email).lower()
            ]
        except Exception:
            support_messages = []
    data["support_messages"] = support_messages

    all_users = list(user_collection.find({}))
    data["referred_users"] = [
        user for user in all_users
        if str(user.get("referred_by") or "") == str(user_id)
    ]
    data["assets"] = _collect_cloudinary_assets(
        data.get("user") or {},
        data["partners"],
        data["vendors"],
        data["distributors"],
    )
    return data


def _delete_user_related(user_id, related, delete_requests=True):
    counts = {}
    main_collections = {
        "portfolio": portfolio_collection,
        "transactions": transactions_collection,
        "orders": orders_collection,
        "reviews": reviews_collection,
        "partners": partners_collection,
        "vendors": vendors_collection,
        "jard_kidz": jard_kidz_collection,
        "notifications": notifications_collection,
    }
    for key, collection in main_collections.items():
        try:
            collection.delete_many({"user_id": user_id})
        except Exception as exc:
            logger.error(f"Delete {key} for user {user_id} failed: {exc}")
            for doc in related.get(key, []):
                try:
                    collection.delete_one({"_id": doc.get("_id")})
                except Exception as inner_exc:
                    logger.error(f"Fallback delete {key}/{doc.get('_id')} failed: {inner_exc}")
        counts[key] = len(related.get(key, []))

    for key in ("housing_subscriptions", "distributors"):
        collection = (
            housing_subscriptions_collection
            if key == "housing_subscriptions"
            else db.get_collection("distributors")
        )
        for doc in related.get(key, []):
            collection.delete_one({"_id": doc.get("_id")})
        counts[key] = len(related.get(key, []))

    aux_collections = {
        "deletion_requests": "deletion_requests",
        "withdraw_requests": "withdraw_requests",
        "kids_profiles": "kids_profiles",
        "land_purchases": "land_purchases",
        "land_payments": "land_payments",
        "land_referral_relationships": "land_referral_relationships",
        "referral_activities": "referral_activities",
        "referral_commissions": "referral_commissions",
        "reports": "reports",
        "support_messages": "support_messages",
    }
    for key, collection_name in aux_collections.items():
        if key == "deletion_requests" and not delete_requests:
            counts[key] = 0
            continue
        try:
            collection = db_aux.get_collection(collection_name)
        except Exception:
            counts[key] = 0
            continue
        docs = related.get(key, [])
        for doc in docs:
            collection.delete_one({"_id": doc.get("_id")})
        counts[key] = len(docs)

    referred_cleared = 0
    for other_user in related.get("referred_users", []):
        try:
            user_collection.update_one(
                {"_id": other_user.get("_id")},
                {"$unset": {"referred_by": ""}},
            )
            referred_cleared += 1
        except Exception as exc:
            logger.error(f"Could not clear referral for {other_user.get('_id')}: {exc}")
    counts["referred_users_cleared"] = referred_cleared

    for asset in related.get("assets", []):
        try:
            delete_from_cloudinary(asset)
        except Exception as exc:
            logger.error(f"Cloudinary cleanup failed for {asset}: {exc}")
    counts["cloudinary_assets"] = len(related.get("assets", []))
    return counts


@router.get("/users/{user_id}/deletion-preview")
async def user_deletion_preview(user_id: str, admin: dict = Depends(verify_admin_token)):
    try:
        user = user_collection.find_one({"_id": user_id})
        if not user:
            return JSONResponse({"message": "User not found", "status": 404}, status_code=404)
        related = _collect_user_related(user_id, user.get("email", ""))
        related["user"] = user
        related["assets"] = _collect_cloudinary_assets(
            user,
            related.get("partners", []),
            related.get("vendors", []),
            related.get("distributors", []),
        )
        return JSONResponse({
            "message": "Deletion preview fetched",
            "status": 200,
            "data": {
                "user": {
                    "_id": user.get("_id"),
                    "user_name": user.get("user_name", ""),
                    "email": user.get("email", ""),
                    "phone_number": user.get("phone_number", ""),
                    "wallet_balance": float(user.get("wallet_balance", 0) or 0),
                    "earnings_balance": float(user.get("earnings_balance", 0) or 0),
                },
                "confirmation": f"DELETE {user.get('email', '')}",
                "counts": {
                    **{key: len(value) for key, value in related.items() if isinstance(value, list) and key not in ("user", "assets")},
                    "assets": len(related.get("assets", [])),
                },
            },
        })
    except Exception as e:
        logger.error(f"Error building deletion preview for {user_id}: {e}")
        return JSONResponse({"message": str(e), "status": 500}, status_code=500)


@router.delete("/delete-user/{user_id}")
async def delete_user(user_id: str, payload: dict = None, admin: dict = Depends(verify_admin_token)):
    try:
        payload = payload or {}
        mode = payload.get("mode")
        confirmation = str(payload.get("confirmation") or "").strip()
        if mode not in {"user_only", "full"}:
            return JSONResponse({"message": "Select a valid deletion mode", "status": 400}, status_code=400)

        user = user_collection.find_one({"_id": user_id})
        if not user:
            return JSONResponse({"message": "User not found", "status": 404}, status_code=404)

        expected_confirmation = f"DELETE {user.get('email', '')}"
        if confirmation != expected_confirmation:
            return JSONResponse({"message": "Confirmation text does not match", "status": 400}, status_code=400)

        if mode == "user_only":
            user_collection.delete_one({"_id": user_id})
            logger.info(f"Admin {admin.get('_id') or admin.get('id')} deleted user only: {user_id}")
            return JSONResponse({
                "message": "User account deleted. Related records were kept.",
                "status": 200,
                "mode": "user_only",
            })

        related = _collect_user_related(user_id, user.get("email", ""))
        related["user"] = user
        asset_urls = _collect_cloudinary_assets(
            user,
            related.get("partners", []),
            related.get("vendors", []),
            related.get("distributors", []),
        )
        related["assets"] = asset_urls
        counts = _delete_user_related(user_id, related, delete_requests=True)
        user_collection.delete_one({"_id": user_id})
        logger.info(f"Admin {admin.get('_id') or admin.get('id')} fully deleted user {user_id} with counts {counts}")
        return JSONResponse({
            "message": "User and all related records deleted",
            "status": 200,
            "mode": "full",
            "counts": counts,
        })
    except Exception as e:
        logger.error(f"Error deleting user {user_id}: {e}")
        return JSONResponse({"message": str(e), "status": 500}, status_code=500)

# --- ACCOUNT DELETION REQUESTS ---

@router.get("/deletion-requests")
async def get_deletion_requests():
    try:
        docs = list(deletion_requests_collection.find({}).sort({"created_at": -1}))
        data = []
        for d in docs:
            d["_id"] = str(d["_id"])
            d.pop("otp_plain", None)
            data.append(d)
        return JSONResponse({"message": "Deletion requests fetched", "data": data, "status": 200})
    except Exception as e:
        logger.error(f"Error fetching deletion requests: {e}")
        return JSONResponse({"message": str(e), "status": 500})

@router.post("/deletion-requests/{request_id}/approve")
async def approve_deletion_request(request_id: str, payload: dict = None, admin: dict = Depends(verify_admin_token)):
    try:
        record = deletion_requests_collection.find_one({"id": request_id})
        if not record:
            return JSONResponse({"message": "Deletion request not found", "status": 404})

        if record.get("state") in ("approved", "rejected"):
            return JSONResponse({"message": f"Request already {record.get('state')}", "status": 400})

        user_id = str(record.get("user_id") or "")
        deleted = False
        counts = {}
        if user_id:
            user = user_collection.find_one({"_id": user_id})
            if user:
                expected = f"DELETE {user.get('email', '')}"
                if str((payload or {}).get("confirmation") or "").strip() != expected:
                    return JSONResponse({"message": "Confirmation text does not match", "status": 400})
                related = _collect_user_related(user_id, user.get("email", ""))
                related["user"] = user
                related["assets"] = _collect_cloudinary_assets(
                    user,
                    related.get("partners", []),
                    related.get("vendors", []),
                    related.get("distributors", []),
                )
                counts = _delete_user_related(user_id, related, delete_requests=False)
                result = user_collection.delete_one({"_id": user_id})
                deleted = result.deleted_count > 0
            else:
                deleted = False

        now_iso = datetime.datetime.utcnow().isoformat()
        deletion_requests_collection.update_one(
            {"id": request_id},
            {"$set": {
                "state": "approved",
                "processed_at": now_iso,
                "updated_at": now_iso,
                "user_deleted": deleted,
                "deletion_counts": counts,
                "processed_by": str(admin.get("_id") or admin.get("id") or ""),
            }},
        )
        logger.info(f"Admin APPROVED deletion request {request_id} | user {user_id} deleted={deleted}")
        return JSONResponse({"message": "Account deleted and request approved", "status": 200, "user_deleted": deleted, "counts": counts})
    except Exception as e:
        logger.error(f"Error approving deletion request {request_id}: {e}")
        return JSONResponse({"message": str(e), "status": 500})

@router.post("/deletion-requests/{request_id}/reject")
async def reject_deletion_request(request_id: str, data: dict = None):
    try:
        record = deletion_requests_collection.find_one({"id": request_id})
        if not record:
            return JSONResponse({"message": "Deletion request not found", "status": 404})

        if record.get("state") in ("approved", "rejected"):
            return JSONResponse({"message": f"Request already {record.get('state')}", "status": 400})

        now_iso = datetime.datetime.utcnow().isoformat()
        note = (data or {}).get("note", "")
        deletion_requests_collection.update_one(
            {"id": request_id},
            {"$set": {
                "state": "rejected",
                "admin_note": note,
                "processed_at": now_iso,
                "updated_at": now_iso,
            }},
        )
        logger.info(f"Admin REJECTED deletion request {request_id}")
        return JSONResponse({"message": "Deletion request rejected", "status": 200})
    except Exception as e:
        logger.error(f"Error rejecting deletion request {request_id}: {e}")
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

@router.get("/housing-subscriptions")
async def get_housing_subscriptions(
    estate_id: str = None,
    user_id: str = None,
    subscription_status: str = None,
    search: str = None,
):
    try:
        subscriptions = list(housing_subscriptions_collection.find({}))
        if estate_id:
            subscriptions = [sub for sub in subscriptions if str(sub.get("estate_id")) == str(estate_id)]
        if user_id:
            subscriptions = [sub for sub in subscriptions if str(sub.get("user_id")) == str(user_id)]
        if subscription_status:
            subscriptions = [sub for sub in subscriptions if sub.get("status") == subscription_status]

        enriched = []
        for sub in subscriptions:
            sub["_id"] = str(sub["_id"])
            user = user_collection.find_one(
                {"_id": sub.get("user_id")},
                projection={"user_name": 1, "email": 1, "phone_number": 1},
            )
            sub["user_info"] = user or {
                "user_name": sub.get("user_name", ""),
                "email": sub.get("user_email", ""),
                "phone_number": sub.get("user_phone", ""),
            }
            house = house_collection.find_one(
                {"_id": sub.get("estate_id")},
                projection={"house_name": 1, "house_location": 1},
            )
            sub["estate_info"] = house or {
                "house_name": sub.get("house_name", ""),
                "house_location": sub.get("house_location", ""),
            }
            enriched.append(sub)

        if search:
            term = search.strip().lower()
            enriched = [
                sub for sub in enriched
                if term in str(sub.get("user_info", {}).get("user_name", "")).lower()
                or term in str(sub.get("user_info", {}).get("email", "")).lower()
                or term in str(sub.get("estate_info", {}).get("house_name", "")).lower()
                or term in str(sub.get("unit_type_name", "")).lower()
                or term in str(sub.get("subscription_id", "")).lower()
            ]

        enriched.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return JSONResponse({
            "message": "Housing subscriptions fetched",
            "data": enriched,
            "status": 200,
        })
    except Exception as e:
        logger.error(f"Error fetching housing subscriptions: {e}")
        return JSONResponse({"message": str(e), "status": 500}, status_code=500)


@router.patch("/housing-subscriptions/{subscription_id}/status")
async def update_housing_subscription_status(subscription_id: str, payload: dict):
    try:
        new_status = (payload or {}).get("status")
        if new_status not in {"Active", "Suspended", "Completed", "Cancelled"}:
            return JSONResponse({"message": "Invalid status", "status": 400}, status_code=400)
        existing = housing_subscriptions_collection.find_one({"_id": subscription_id})
        if not existing:
            return JSONResponse({"message": "Subscription not found", "status": 404}, status_code=404)
        housing_subscriptions_collection.update_one(
            {"_id": subscription_id},
            {"$set": {
                "status": new_status,
                "updated_at": datetime.datetime.utcnow().isoformat(),
            }},
        )
        plans = list(portfolio_collection.find({"user_id": existing.get("user_id")}))
        for plan in plans:
            if plan.get("housing_subscription_id") == subscription_id:
                portfolio_collection.update_one(
                    {"_id": plan.get("_id")},
                    {"$set": {"status": new_status}},
                )
        return JSONResponse({"message": "Subscription status updated", "status": 200})
    except Exception as e:
        logger.error(f"Error updating housing subscription: {e}")
        return JSONResponse({"message": str(e), "status": 500}, status_code=500)


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


# ─────────────────────────────────────────────
# SUBSCRIPTION SETTINGS (Admin Configurable)
# ─────────────────────────────────────────────

@router.get("/subscription-settings")
async def get_subscription_settings():
    try:
        from scheduler import get_settings
        settings = get_settings()
        settings.pop("_id", None)
        return JSONResponse({"message": "Settings fetched", "data": settings, "status": 200})
    except Exception as e:
        logger.error(f"Error fetching subscription settings: {e}")
        return JSONResponse({"message": str(e), "status": 500})


@router.put("/subscription-settings")
async def update_subscription_settings(payload: dict):
    try:
        existing = sub_settings_collection.find_one({})
        if not existing:
            existing = {}
        existing.pop("_id", None)

        def _num(value, field):
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"{field} must be a number")
            if parsed != parsed:  # reject NaN
                raise ValueError(f"{field} must be a number")
            return parsed

        if "vendor_fee" in payload:
            fee = _num(payload["vendor_fee"], "vendor_fee")
            if fee <= 0:
                return JSONResponse({"message": "Vendor fee must be greater than 0", "status": 400})
            existing["vendor_fee"] = fee

        if "partner_fee" in payload:
            fee = _num(payload["partner_fee"], "partner_fee")
            if fee <= 0:
                return JSONResponse({"message": "Partner fee must be greater than 0", "status": 400})
            existing["partner_fee"] = fee

        if "sub_duration_months" in payload:
            months = int(_num(payload["sub_duration_months"], "sub_duration_months"))
            if months < 1:
                return JSONResponse({"message": "Subscription duration must be at least 1 month", "status": 400})
            existing["sub_duration_months"] = months

        if "penalty_percentage" in payload:
            penalty = _num(payload["penalty_percentage"], "penalty_percentage")
            if penalty < 0:
                return JSONResponse({"message": "Penalty percentage cannot be negative", "status": 400})
            existing["penalty_percentage"] = penalty

        if "support_whatsapp" in payload:
            raw = str(payload.get("support_whatsapp") or "").strip()
            digits = "".join(ch for ch in raw if ch.isdigit())
            if not raw or not digits:
                return JSONResponse({"message": "Support WhatsApp number is required", "status": 400})
            existing["support_whatsapp"] = digits

        if "housing_registration_fee" in payload:
            fee = _num(payload["housing_registration_fee"], "housing_registration_fee")
            if fee < 0:
                return JSONResponse({"message": "Housing registration fee cannot be negative", "status": 400})
            existing["housing_registration_fee"] = fee

        if "housing_savings_multiplier" in payload:
            multiplier = _num(payload["housing_savings_multiplier"], "housing_savings_multiplier")
            if multiplier <= 0:
                return JSONResponse({"message": "Housing savings multiplier must be greater than 0", "status": 400})
            existing["housing_savings_multiplier"] = multiplier

        sub_settings_collection.replace_one({}, existing, upsert=True)
        return JSONResponse({"message": "Settings updated", "data": existing, "status": 200})
    except ValueError as ve:
        return JSONResponse({"message": str(ve), "status": 400})
    except Exception as e:
        logger.error(f"Error updating subscription settings: {e}")
        return JSONResponse({"message": str(e), "status": 500})


@router.post("/force-check-subscriptions")
async def force_check_subscriptions():
    try:
        from scheduler import run_all
        import threading
        thread = threading.Thread(target=run_all, daemon=True)
        thread.start()
        return JSONResponse({"message": "Subscription check triggered in background", "status": 200})
    except Exception as e:
        logger.error(f"Error in force-check: {e}")
        return JSONResponse({"message": str(e), "status": 500})


@router.get("/subscriptions-list")
async def get_subscriptions_list(type: str = "all"):
    try:
        vendors = list(vendors_collection.find({}))
        partners = list(partners_collection.find({}))
        result = []
        for v in vendors:
            v["_id"] = str(v["_id"])
            v["subscription_type"] = "vendor"
            user = user_collection.find_one({"_id": v["user_id"]}, projection={"user_name": 1, "email": 1})
            if user:
                v["user_info"] = {"name": user.get("user_name"), "email": user.get("email")}
            result.append(v)
        for p in partners:
            p["_id"] = str(p["_id"])
            p["subscription_type"] = "partner"
            user = user_collection.find_one({"_id": p["user_id"]}, projection={"user_name": 1, "email": 1})
            if user:
                p["user_info"] = {"name": user.get("user_name"), "email": user.get("email")}
            result.append(p)
        return JSONResponse({"message": "Subscriptions fetched", "data": result, "status": 200})
    except Exception as e:
        logger.error(f"Error fetching subscriptions list: {e}")
        return JSONResponse({"message": str(e), "status": 500})



