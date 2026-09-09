from fastapi import APIRouter,Depends,HTTPException,status,BackgroundTasks, Request, UploadFile, File
from db.database import user_collection, house_collection, transactions_collection, jard_kidz_collection, vendors_collection, partners_collection, notifications_collection, sub_settings_collection, reports_collection, reviews_collection, withdraw_requests_collection
import datetime
import secrets
from utill import hashedpassword,VerifyHashed,create_access_token,get_token,process_base85_image,reassemble_base64_string, send_jard_kidz_email, send_wallet_credit_email, get_image_url, upload_to_cloudinary, delete_from_cloudinary

from fastapi.responses import JSONResponse, Response
from utill import generate_otp,get_token,send_email
from scheduler import get_settings, _encrypt_pin
from dateutil.relativedelta import relativedelta
import base64
from starlette import status
import logging
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
import os
from model import User, Login, GoogleAuth, JardKidzPlan, ForgotPassword, ResetPassword, JardAccount
import shutil
import time

logger = logging.getLogger("jardx")

router = APIRouter(prefix="/users", tags=["Users"])

@router.post("/vendor-register")
async def vendor_register(vendor: JardAccount, data: dict = Depends(get_token)):
    try:
        user_id = data["id"]
        user_data = user_collection.find_one({"_id": user_id})
        
        if not user_data:
            return JSONResponse({"message": "User not found", "status": 404})
            
        settings = get_settings()
        fee = float(settings.get("vendor_fee", 25000))
        if user_data.get("wallet_balance", 0) < fee:
            return JSONResponse({"message": "Insufficient balance", "status": 400})
            
        photo_url = upload_to_cloudinary(vendor.photo, f"vendors/photos/{user_id}")
        cert_url = upload_to_cloudinary(vendor.certificate, f"vendors/certs/{user_id}")
            
        expiry = (datetime.datetime.utcnow() + relativedelta(months=int(settings.get("sub_duration_months", 12)))).isoformat()

        vendor_record = vendor.dict()
        vendor_record["user_id"] = user_id
        vendor_record["photo"] = photo_url
        vendor_record["certificate"] = cert_url
        vendor_record["accountType"] = "vendor"
        vendor_record["status"] = "unverified"
        vendor_record["created_at"] = datetime.datetime.utcnow().isoformat()
        vendor_record["subscription_start"] = datetime.datetime.utcnow().isoformat()
        vendor_record["subscription_expiry"] = expiry
        vendor_record["subscription_status"] = "active"
        
        vendors_collection.insert_one(vendor_record)
        
        new_balance = float(user_data["wallet_balance"]) - fee
        user_collection.update_one({"_id": user_id}, {"$set": {
            "wallet_balance": new_balance,
            "partner_type": "vendor"
        }})
        
        transactions_collection.insert_one({
            "tx_ref": f"VNDR-{secrets.token_hex(6).upper()}",
            "user_id": user_id,
            "amount": float(fee),
            "type": "DEBIT",
            "purpose": "Vendor Registration Fee",
            "status": "SUCCESS",
            "created_at": datetime.datetime.utcnow().isoformat()
        })
        
        return JSONResponse({"message": "Registration successful", "status": 200})
    except Exception as e:
        logger.error(f"Error in /vendor-register: {e}")
        return JSONResponse({"message": str(e), "status": 500}, status_code=500)

@router.get("/vendor-details")
async def get_vendor_details(data: dict = Depends(get_token)):
    try:
        user_id = data["id"]
        vendor_data = vendors_collection.find_one({"user_id": user_id})
        
        if not vendor_data:
            return JSONResponse({"message": "Vendor record not found", "status": 404}, status_code=404)
            
        # Convert _id to string
        vendor_data["_id"] = str(vendor_data["_id"])
        
        return JSONResponse({"status": 200, "data": vendor_data}, status_code=200)
    except Exception as e:
        logger.error(f"Error in /vendor-details: {e}")
        return JSONResponse({"message": str(e), "status": 500}, status_code=500)

@router.get("/vendor-subscription")
async def get_vendor_subscription(data: dict = Depends(get_token)):
    try:
        user_id = data["id"]
        vendor_data = vendors_collection.find_one({"user_id": user_id})
        
        if not vendor_data:
            return JSONResponse({
                "status": 200,
                "data": {
                    "subscription_status": "none",
                    "subscription_expiry": None,
                }
            }, status_code=200)
        
        subscription_expiry = vendor_data.get("subscription_expiry")
        subscription_status = vendor_data.get("subscription_status", "active")

        settings = get_settings()

        if not subscription_expiry:
            subscription_expiry = (datetime.datetime.utcnow() + relativedelta(months=int(settings.get("sub_duration_months", 12)))).isoformat()
            subscription_status = "active"
            vendors_collection.update_one(
                {"user_id": user_id},
                {"$set": {
                    "subscription_expiry": subscription_expiry,
                    "subscription_status": "active",
                    "subscription_start": datetime.datetime.utcnow().isoformat(),
                }}
            )
        
        if subscription_expiry:
            expiry_dt = datetime.datetime.fromisoformat(subscription_expiry)
            now = datetime.datetime.utcnow()
            if now >= expiry_dt:
                if subscription_status == "active":
                    vendors_collection.update_one(
                        {"user_id": user_id},
                        {"$set": {"subscription_status": "grace"}}
                    )
                    subscription_status = "grace"
                elif subscription_status == "grace":
                    grace_end = expiry_dt + datetime.timedelta(days=7)
                    if now >= grace_end:
                        vendors_collection.update_one(
                            {"user_id": user_id},
                            {"$set": {"subscription_status": "expired"}}
                        )
                        subscription_status = "expired"
        
        return JSONResponse({
            "status": 200,
            "data": {
                "subscription_expiry": subscription_expiry,
                "subscription_status": subscription_status,
            }
        }, status_code=200)
    except Exception as e:
        logger.error(f"Error in /vendor-subscription: {e}")
        return JSONResponse({"message": str(e), "status": 500}, status_code=500)

@router.post("/vendor-subscription-renew")
async def renew_vendor_subscription(data: dict = Depends(get_token)):
    try:
        user_id = data["id"]
        user_data = user_collection.find_one({"_id": user_id})
        
        if not user_data:
            return JSONResponse({"message": "User not found", "status": 404})
        
        settings = get_settings()
        fee = float(settings.get("vendor_fee", 25000))
        if user_data.get("wallet_balance", 0) < fee:
            return JSONResponse({"message": "Insufficient balance", "status": 400})
        
        new_expiry = (datetime.datetime.utcnow() + relativedelta(months=int(settings.get("sub_duration_months", 12)))).isoformat()
        
        vendors_collection.update_one(
            {"user_id": user_id},
            {"$set": {
                "subscription_expiry": new_expiry,
                "subscription_status": "active",
            }}
        )
        
        user_collection.update_one(
            {"_id": user_id, "wallet_balance": {"$gte": fee}},
            {"$inc": {"wallet_balance": -fee}}
        )
        
        updated_user = user_collection.find_one({"_id": user_id})
        actual_balance = float(updated_user["wallet_balance"])
        
        transactions_collection.insert_one({
            "tx_ref": f"SUB-{secrets.token_hex(6).upper()}",
            "user_id": user_id,
            "amount": float(fee),
            "gateway": "Wallet",
            "type": "DEBIT",
            "purpose": "Vendor Subscription Renewal",
            "status": "SUCCESS",
            "created_at": datetime.datetime.utcnow().isoformat()
        })
        
        return JSONResponse({
            "status": 200,
            "data": {
                "subscription_expiry": new_expiry,
                "subscription_status": "active",
                "wallet_balance": actual_balance,
            }
        }, status_code=200)
    except Exception as e:
        logger.error(f"Error in /vendor-subscription-renew: {e}")
        return JSONResponse({"message": str(e), "status": 500}, status_code=500)

@router.post("/toggle-vendor-auto-debit")
async def toggle_vendor_auto_debit(payload: dict, data: dict = Depends(get_token)):
    try:
        user_id = data.get("id")
        if not user_id:
            return JSONResponse(status_code=401, content={"message": "Invalid user"})
        vendor = vendors_collection.find_one({"user_id": user_id})
        if not vendor:
            return JSONResponse(status_code=404, content={"message": "Vendor record not found"})
        enable = payload.get("enable", False)
        pin = payload.get("pin", "")
        if enable:
            if not pin or len(str(pin)) != 4:
                return JSONResponse(status_code=400, content={"message": "Valid 4-digit PIN required"})
            user = user_collection.find_one({"_id": user_id})
            if not user or not user.get("transaction_pin"):
                return JSONResponse(status_code=400, content={"message": "No transaction PIN set"})
            try:
                valid = VerifyHashed(user["transaction_pin"], str(pin))
                if not valid:
                    return JSONResponse(status_code=401, content={"message": "Incorrect PIN"})
            except Exception:
                return JSONResponse(status_code=401, content={"message": "Incorrect PIN"})
            encrypted = _encrypt_pin(str(pin))
            vendors_collection.update_one(
                {"user_id": user_id},
                {"$set": {"auto_debit": True, "encrypted_pin": encrypted}}
            )
            return JSONResponse(status_code=200, content={"message": "Auto-renewal enabled", "status": 200})
        else:
            vendors_collection.update_one(
                {"user_id": user_id},
                {"$set": {"auto_debit": False, "encrypted_pin": ""}}
            )
            return JSONResponse(status_code=200, content={"message": "Auto-renewal disabled", "status": 200})
    except Exception as e:
        logger.error(f"Toggle vendor auto-debit error: {e}")
        return JSONResponse(status_code=500, content={"message": str(e)})

@router.get("/vendors")
async def get_all_vendors():
    try:
        vendors = list(vendors_collection.find({}))
        all_vendor_reviews = list(reviews_collection.find({"vendor_id": {"$ne": ""}}))
        rating_map = {}
        for r in all_vendor_reviews:
            vid = str(r.get("vendor_id", ""))
            if not vid:
                continue
            if vid not in rating_map:
                rating_map[vid] = {"total": 0, "sum": 0}
            rating_map[vid]["total"] += 1
            try:
                rating_map[vid]["sum"] += int(r.get("rating", 0))
            except (TypeError, ValueError):
                pass
        for v in vendors:
            v["_id"] = str(v["_id"])
            if not v.get("photo"):
                v["photo"] = "https://images.unsplash.com/photo-1544005313-94ddf0286df2?q=80&w=200"
            vid = str(v.get("user_id", ""))
            if vid in rating_map and rating_map[vid]["total"] > 0:
                avg = round(rating_map[vid]["sum"] / rating_map[vid]["total"], 1)
                v["average_rating"] = avg
                v["total_reviews"] = rating_map[vid]["total"]
            else:
                v["average_rating"] = None
                v["total_reviews"] = 0
        return JSONResponse({"status": 200, "data": vendors}, status_code=200)
    except Exception as e:
        logger.error(f"Error in /vendors: {e}")
        return JSONResponse({"message": str(e), "status": 500}, status_code=500)


@router.get("/vendors/{vendor_id}/rating")
async def get_vendor_rating(vendor_id: str):
    try:
        reviews = list(reviews_collection.find({"vendor_id": vendor_id}))
        total = len(reviews)
        distribution = {5: 0, 4: 0, 3: 0, 2: 0, 1: 0}
        total_rating = 0
        for r in reviews:
            try:
                star = int(r.get("rating", 0))
            except (TypeError, ValueError):
                continue
            if star in distribution:
                distribution[star] += 1
            total_rating += star
        average = round(total_rating / total, 1) if total > 0 else 0
        return JSONResponse({
            "data": {"average": average, "total": total, "distribution": distribution},
            "status": 200
        })
    except Exception as e:
        logger.error(f"Error in /vendors/{{vendor_id}}/rating: {e}")
        return JSONResponse({"message": str(e), "status": 500})


@router.get("/vendors/{vendor_id}/reviews")
async def get_vendor_reviews(vendor_id: str):
    try:
        reviews = list(
            reviews_collection.find({"vendor_id": vendor_id}).sort({"created_at": -1})
        )
        for r in reviews:
            r["_id"] = str(r["_id"])
        return JSONResponse({"message": "Reviews fetched", "data": reviews, "status": 200})
    except Exception as e:
        logger.error(f"Error in /vendors/{{vendor_id}}/reviews: {e}")
        if "CORRUPTED_COLLECTION_SCHEMA" in str(e):
            return JSONResponse({"message": "Reviews fetched", "data": [], "status": 200})
        return JSONResponse({"message": str(e), "status": 500})


@router.post("/vendors/{vendor_id}/reviews")
async def add_vendor_review(vendor_id: str, payload: dict, user_payload: dict = Depends(get_token)):
    try:
        user_id = user_payload.get("id")
        user = user_collection.find_one({"_id": user_id})

        try:
            rating_value = int(payload.get("rating", 0))
        except (TypeError, ValueError):
            return JSONResponse({"message": "Rating must be a number", "status": 400})
        if rating_value < 1 or rating_value > 5:
            return JSONResponse({"message": "Rating must be between 1 and 5", "status": 400})

        comment = (payload.get("comment") or "").strip()
        if not comment:
            return JSONResponse({"message": "Comment is required", "status": 400})

        vendor = vendors_collection.find_one({"user_id": vendor_id})
        vendor_name = (vendor.get("fullName") or vendor.get("name")) if vendor else ""

        review_dict = {
            "product_id": "",
            "vendor_id": vendor_id,
            "vendor_name": vendor_name,
            "user_id": user_id,
            "user_name": user.get("user_name", "User") if user else "User",
            "rating": rating_value,
            "comment": comment,
            "created_at": datetime.datetime.utcnow().isoformat()
        }
        reviews_collection.insert_one(review_dict)
        return JSONResponse({"message": "Review added successfully", "status": 200})
    except Exception as e:
        logger.error(f"Error in POST /vendors/{{vendor_id}}/reviews: {e}")
        return JSONResponse({"message": str(e), "status": 500})


@router.put("/vendors/reviews/{review_id}")
async def update_vendor_review(review_id: str, payload: dict, user_payload: dict = Depends(get_token)):
    try:
        user_id = user_payload.get("id")
        existing = reviews_collection.find_one({"_id": review_id})
        if not existing:
            return JSONResponse({"message": "Review not found", "status": 404})
        if str(existing.get("user_id")) != str(user_id):
            return JSONResponse({"message": "You can only edit your own review", "status": 403})
        if not existing.get("vendor_id"):
            return JSONResponse({"message": "Not a vendor review", "status": 400})

        try:
            rating_value = int(payload.get("rating", 0))
        except (TypeError, ValueError):
            return JSONResponse({"message": "Rating must be a number", "status": 400})
        if rating_value < 1 or rating_value > 5:
            return JSONResponse({"message": "Rating must be between 1 and 5", "status": 400})

        comment = (payload.get("comment") or "").strip()
        if not comment:
            return JSONResponse({"message": "Comment is required", "status": 400})

        reviews_collection.update_one(
            {"_id": review_id},
            {"$set": {
                "rating": rating_value,
                "comment": comment,
                "updated_at": datetime.datetime.utcnow().isoformat()
            }}
        )
        return JSONResponse({"message": "Review updated successfully", "status": 200})
    except Exception as e:
        logger.error(f"Error in PUT /vendors/reviews/{{review_id}}: {e}")
        return JSONResponse({"message": str(e), "status": 500})


@router.delete("/vendors/reviews/{review_id}")
async def delete_vendor_review(review_id: str, user_payload: dict = Depends(get_token)):
    try:
        user_id = user_payload.get("id")
        existing = reviews_collection.find_one({"_id": review_id})
        if not existing:
            return JSONResponse({"message": "Review not found", "status": 404})
        if str(existing.get("user_id")) != str(user_id):
            return JSONResponse({"message": "You can only delete your own review", "status": 403})
        if not existing.get("vendor_id"):
            return JSONResponse({"message": "Not a vendor review", "status": 400})
        reviews_collection.delete_one({"_id": review_id})
        return JSONResponse({"message": "Review deleted successfully", "status": 200})
    except Exception as e:
        logger.error(f"Error in DELETE /vendors/reviews/{{review_id}}: {e}")
        return JSONResponse({"message": str(e), "status": 500})

@router.post("/report-vendor")
async def report_vendor(payload: dict, data: dict = Depends(get_token)):
    try:
        user_id = data.get("id")
        vendor_id = payload.get("vendor_id")
        reason = payload.get("reason")
        custom_reason = payload.get("custom_reason", "")

        if not vendor_id or not reason:
            return JSONResponse({"message": "Vendor ID and reason are required", "status": 400})

        reports_collection.insert_one({
            "reporter_id": user_id,
            "vendor_id": vendor_id,
            "reason": reason,
            "custom_reason": custom_reason,
            "status": "pending",
            "created_at": datetime.datetime.utcnow().isoformat()
        })
        return JSONResponse({"message": "Report submitted successfully", "status": 200})
    except Exception as e:
        logger.error(f"Error in /report-vendor: {e}")
        return JSONResponse({"message": str(e), "status": 500})

@router.post("/update-vendor-photo")
async def update_vendor_photo(payload: dict, data: dict = Depends(get_token)):
    try:
        user_id = data["id"]
        photo_base64 = payload.get("photo")
        
        if not photo_base64:
            return JSONResponse({"message": "No photo data provided", "status": 400}, status_code=400)
            
        # Upload to Cloudinary
        photo_url = upload_to_cloudinary(photo_base64, f"vendors/photos/{user_id}")
        
        if not photo_url:
            return JSONResponse({"message": "Upload failed", "status": 500}, status_code=500)
            
        # Update database
        vendors_collection.update_one(
            {"user_id": user_id},
            {"$set": {"photo": photo_url}}
        )
        
        return JSONResponse({"message": "Photo updated successfully", "url": photo_url, "status": 200}, status_code=200)
    except Exception as e:
        logger.error(f"Error in /update-vendor-photo: {e}")
        return JSONResponse({"message": str(e), "status": 500}, status_code=500)

@router.post("/update-vendor-details")
async def update_vendor_details(payload: dict, data: dict = Depends(get_token)):
    try:
        user_id = data["id"]
        vendor_data = vendors_collection.find_one({"user_id": user_id})
        if not vendor_data:
            return JSONResponse({"message": "Vendor record not found", "status": 404}, status_code=404)
        
        allowed_fields = [
            "fullName", "phone", "address", "vocation", "profession",
            "accountNumber", "bankName", "description", "workExperience"
        ]
        updates = {}
        for field in allowed_fields:
            if field in payload:
                updates[field] = payload[field]
                
        if updates:
            vendors_collection.update_one({"user_id": user_id}, {"$set": updates})
            
        return JSONResponse({"message": "Vendor details updated successfully", "status": 200}, status_code=200)
    except Exception as e:
        logger.error(f"Error in /update-vendor-details: {e}")
        return JSONResponse({"message": str(e), "status": 500}, status_code=500)

@router.post("/add-vendor-gallery")
async def add_vendor_gallery(payload: dict, data: dict = Depends(get_token)):
    try:
        user_id = data["id"]
        photo_base64 = payload.get("photo")
        if not photo_base64:
            return JSONResponse({"message": "No photo data provided", "status": 400}, status_code=400)
            
        vendor_data = vendors_collection.find_one({"user_id": user_id})
        if not vendor_data:
            return JSONResponse({"message": "Vendor record not found", "status": 404}, status_code=404)
            
        gallery = vendor_data.get("gallery", [])
        if len(gallery) >= 4:
            return JSONResponse({"message": "Maximum of 4 gallery pictures allowed", "status": 400}, status_code=400)
            
        # Upload to Cloudinary
        # Keep gallery files unique per vendor by folder naming
        photo_url = upload_to_cloudinary(photo_base64, f"vendors/gallery/{user_id}")
        
        if not photo_url:
            return JSONResponse({"message": "Upload failed", "status": 500}, status_code=500)
            
        # Duplicate check: check if the exact URL is already in the gallery
        if photo_url in gallery:
            return JSONResponse({"message": "This image already exists in your gallery", "status": 400, "url": photo_url}, status_code=400)
            
        gallery.append(photo_url)
        vendors_collection.update_one({"user_id": user_id}, {"$set": {"gallery": gallery}})
        
        return JSONResponse({"message": "Gallery image added successfully", "url": photo_url, "gallery": gallery, "status": 200}, status_code=200)
    except Exception as e:
        logger.error(f"Error in /add-vendor-gallery: {e}")
        return JSONResponse({"message": str(e), "status": 500}, status_code=500)

@router.post("/delete-vendor-gallery")
async def delete_vendor_gallery(payload: dict, data: dict = Depends(get_token)):
    try:
        user_id = data["id"]
        photo_url = payload.get("photo_url")
        if not photo_url:
            return JSONResponse({"message": "No photo URL provided", "status": 400}, status_code=400)
            
        vendor_data = vendors_collection.find_one({"user_id": user_id})
        if not vendor_data:
            return JSONResponse({"message": "Vendor record not found", "status": 404}, status_code=404)
            
        gallery = vendor_data.get("gallery", [])
        if photo_url in gallery:
            gallery.remove(photo_url)
            # Delete from Cloudinary
            delete_from_cloudinary(photo_url)
            vendors_collection.update_one({"user_id": user_id}, {"$set": {"gallery": gallery}})
            
        return JSONResponse({"message": "Gallery image deleted successfully", "gallery": gallery, "status": 200}, status_code=200)
    except Exception as e:
        logger.error(f"Error in /delete-vendor-gallery: {e}")
        return JSONResponse({"message": str(e), "status": 500}, status_code=500)


@router.post("/partner-register")
async def partner_register(partner: JardAccount, data: dict = Depends(get_token)):
    try:
        user_id = data["id"]
        user_data = user_collection.find_one({"_id": user_id})
        
        if not user_data:
            return JSONResponse({"message": "User not found", "status": 404})
            
        settings = get_settings()
        fee = float(settings.get("partner_fee", 100000))
        if user_data.get("wallet_balance", 0) < fee:
            return JSONResponse({"message": "Insufficient balance", "status": 400})
            
        photo_url = upload_to_cloudinary(partner.photo, f"partners/photos/{user_id}")
        cert_url = upload_to_cloudinary(partner.certificate, f"partners/certs/{user_id}")
            
        expiry = (datetime.datetime.utcnow() + relativedelta(months=int(settings.get("sub_duration_months", 12)))).isoformat()

        partner_record = partner.dict()
        partner_record["user_id"] = user_id
        partner_record["photo"] = photo_url
        partner_record["certificate"] = cert_url
        partner_record["accountType"] = "partner"
        partner_record["status"] = "unverified"
        partner_record["created_at"] = datetime.datetime.utcnow().isoformat()
        partner_record["subscription_start"] = datetime.datetime.utcnow().isoformat()
        partner_record["subscription_expiry"] = expiry
        partner_record["subscription_status"] = "active"
        
        partners_collection.insert_one(partner_record)
        
        # Update user status
        user_collection.update_one({"_id": user_id}, {"$set": {
            "is_partner": True,
            "partner_type": "partner"
        }})
        
        # Deduct fee
        new_balance = float(user_data["wallet_balance"]) - fee
        user_collection.update_one({"_id": user_id}, {"$set": {"wallet_balance": new_balance}})
        
        # Record transaction
        transactions_collection.insert_one({
            "tx_ref": f"PRTN-{secrets.token_hex(6).upper()}",
            "user_id": user_id,
            "amount": float(fee),
            "type": "DEBIT",
            "purpose": "Partner Registration Fee",
            "status": "SUCCESS",
            "created_at": datetime.datetime.utcnow().isoformat()
        })
        
        return JSONResponse({"message": "Registration successful", "status": 200})
        
    except Exception as e:
        logger.error(f"Error in /partner-register: {e}")
        return JSONResponse({"message": str(e), "status": 500}, status_code=500)

@router.get("/partner-details")
async def get_partner_details(data: dict = Depends(get_token)):
    try:
        user_id = data["id"]
        partner_data = partners_collection.find_one({"user_id": user_id})
        
        if not partner_data:
            return JSONResponse({"message": "Partner record not found", "status": 404}, status_code=404)
            
        # Convert _id to string
        partner_data["_id"] = str(partner_data["_id"])
        
        return JSONResponse({"status": 200, "data": partner_data}, status_code=200)
    except Exception as e:
        logger.error(f"Error in /partner-details: {e}")
        return JSONResponse({"message": str(e), "status": 500}, status_code=500)


@router.get("/partner-dashboard")
async def get_partner_dashboard(data: dict = Depends(get_token)):
    """
    Aggregated payload for the Partner Dashboard screen.
    Returns partner profile, earnings summary, monthly time-series chart,
    referral summary, and per-referral activity.
    """
    try:
        user_id = data["id"]
        uid_str = str(user_id)

        # --- 1. Partner profile ---
        partner_record = partners_collection.find_one({"user_id": uid_str})
        partner_payload = {}
        if partner_record:
            partner_record["_id"] = str(partner_record.get("_id", ""))
            # Strip sensitive / large fields the dashboard doesn't need
            partner_record.pop("certificate", None)
            partner_payload = {
                "fullName": partner_record.get("fullName", ""),
                "email": partner_record.get("email", ""),
                "phone": partner_record.get("phone", ""),
                "address": partner_record.get("address", ""),
                "skills": partner_record.get("skills", ""),
                "profession": partner_record.get("profession", "") or partner_record.get("skills", ""),
                "accountNumber": partner_record.get("accountNumber", ""),
                "bankName": partner_record.get("bankName", ""),
                "photo": partner_record.get("photo") or None,
                "status": partner_record.get("status", "unverified"),
                "description": partner_record.get("description", ""),
                "workExperience": partner_record.get("workExperience", ""),
                "created_at": partner_record.get("created_at", ""),
                "subscription_start": partner_record.get("subscription_start", ""),
                "subscription_expiry": partner_record.get("subscription_expiry", ""),
                "subscription_status": partner_record.get("subscription_status", ""),
            }

        # --- 2. User wallet balance + referral_info ---
        user_record = user_collection.find_one({"_id": uid_str}) or {}
        wallet_balance = float(user_record.get("wallet_balance", 0) or 0)

        referrals = list(user_collection.find(
            {"referred_by": uid_str},
            projection={"user_name": 1, "created_at": 1, "is_referral_active": 1}
        ))

        raw_credits = list(transactions_collection.find({
            "user_id": uid_str,
            "type": "CREDIT"
        }))

        def _is_bonus_purpose(purpose: str) -> bool:
            p = (purpose or "").lower()
            return (
                "referral bonus" in p
                or "partner direct commission" in p
                or "partner indirect commission" in p
            )

        bonus_credits = [tx for tx in raw_credits if _is_bonus_purpose(tx.get("purpose", ""))]

        partner_direct_earned = sum(
            float(tx.get("amount", 0) or 0)
            for tx in bonus_credits
            if tx.get("purpose") == "Partner Direct Commission"
        )
        partner_indirect_earned = sum(
            float(tx.get("amount", 0) or 0)
            for tx in bonus_credits
            if tx.get("purpose") == "Partner Indirect Commission"
        )
        referral_total = partner_direct_earned + partner_indirect_earned

        # Total earnings = all qualifying bonus credits (incl. standard "Referral Bonus" if any)
        total_earnings = sum(float(tx.get("amount", 0) or 0) for tx in bonus_credits)

        # --- 3. Time-series: this month vs last month, plus 6-bar chart ---
        now = datetime.datetime.utcnow()
        first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        first_of_last_month = (first_of_this_month - datetime.timedelta(days=1)).replace(day=1)

        def _parse_dt(value):
            if not value:
                return None
            try:
                if isinstance(value, datetime.datetime):
                    return value
                return datetime.datetime.fromisoformat(str(value).replace("Z", ""))
            except Exception:
                return None

        month_to_date = 0.0
        previous_month = 0.0
        daily_totals = {}

        for tx in bonus_credits:
            tx_dt = _parse_dt(tx.get("created_at", ""))
            if not tx_dt:
                continue
            amt = float(tx.get("amount", 0) or 0)
            if tx_dt >= first_of_this_month:
                month_to_date += amt
                daily_totals[tx_dt.day] = daily_totals.get(tx_dt.day, 0.0) + amt
            elif tx_dt >= first_of_last_month and tx_dt < first_of_this_month:
                previous_month += amt

        # Sample 6 evenly-spaced days; if no activity that day, sum the prior window up to that day
        sample_days = [1, 7, 13, 19, 25, 31]
        monthly_chart = []
        for d in sample_days:
            if daily_totals.get(d):
                monthly_chart.append({"day": d, "amount": round(daily_totals[d], 2)})
            else:
                # Sum all activity from previous sample day up to (and including) this day
                prev_day = sample_days[sample_days.index(d) - 1] if sample_days.index(d) > 0 else 0
                window_total = sum(
                    v for k, v in daily_totals.items()
                    if prev_day < k <= d
                )
                monthly_chart.append({"day": d, "amount": round(window_total, 2)})

        if previous_month > 0:
            trend_pct = round(((month_to_date - previous_month) / previous_month) * 100, 1)
        else:
            trend_pct = 0.0

        # --- 4. Per-referral activity (best-effort match by user_name in purpose) ---
        referral_activity = []
        for ref in referrals:
            ref_name = ref.get("user_name", "")
            ref_id = str(ref.get("_id", ""))
            ref_bonus = 0.0
            for tx in bonus_credits:
                purpose = tx.get("purpose", "") or ""
                if ref_name and ref_name in purpose:
                    ref_bonus += float(tx.get("amount", 0) or 0)
            referral_activity.append({
                "name": ref_name,
                "bonus": round(ref_bonus, 2),
                "is_active": bool(ref.get("is_referral_active", False)),
                "created_at": ref.get("created_at", ""),
                "_id": ref_id,
            })

        # --- 5. Referral summary block ---
        referral_info = {
            "code": user_record.get("referral_code", "") or "",
            "is_active": bool(user_record.get("is_referral_active", False)),
            "percentage": float(user_record.get("referral_percentage", 0) or 0),
            "total_referrals": len(referrals),
            "total_earned": round(total_earnings, 2),
            "total_withdrawn": 0.0,
            "partner_type": user_record.get("partner_type", "normal"),
            "partner_direct_earned": round(partner_direct_earned, 2),
            "partner_indirect_earned": round(partner_indirect_earned, 2),
        }

        # total_withdrawn: sum of DEBIT transactions with referral/partner commission purpose
        raw_debits = list(transactions_collection.find({
            "user_id": uid_str,
            "type": "DEBIT"
        }))
        referral_info["total_withdrawn"] = round(
            sum(
                float(tx.get("amount", 0) or 0)
                for tx in raw_debits
                if "referral" in str(tx.get("purpose", "")).lower()
                or "partner" in str(tx.get("purpose", "")).lower()
            ),
            2
        )

        payload = {
            "partner": partner_payload,
            "earnings": {
                "total": round(total_earnings, 2),
                "referral_total": round(referral_total, 2),
                "referral_direct": round(partner_direct_earned, 2),
                "referral_indirect": round(partner_indirect_earned, 2),
                "available_balance": round(wallet_balance, 2),
                "month_to_date": round(month_to_date, 2),
                "previous_month": round(previous_month, 2),
                "trend_pct": trend_pct,
            },
            "monthly_chart": monthly_chart,
            "referrals": referral_info,
            "referral_activity": referral_activity,
        }

        return JSONResponse({"status": 200, "data": payload}, status_code=200)
    except Exception as e:
        logger.error(f"Error in /partner-dashboard: {e}")
        return JSONResponse({"message": str(e), "status": 500}, status_code=500)


@router.get("/partner-subscription")
async def get_partner_subscription(data: dict = Depends(get_token)):
    try:
        user_id = data["id"]
        partner_data = partners_collection.find_one({"user_id": user_id})
        
        if not partner_data:
            return JSONResponse({
                "status": 200,
                "data": {
                    "subscription_status": "none",
                    "subscription_expiry": None,
                }
            }, status_code=200)
        
        subscription_expiry = partner_data.get("subscription_expiry")
        subscription_status = partner_data.get("subscription_status", "active")

        settings = get_settings()

        if not subscription_expiry:
            subscription_expiry = (datetime.datetime.utcnow() + relativedelta(months=int(settings.get("sub_duration_months", 12)))).isoformat()
            subscription_status = "active"
            partners_collection.update_one(
                {"user_id": user_id},
                {"$set": {
                    "subscription_expiry": subscription_expiry,
                    "subscription_status": "active",
                    "subscription_start": datetime.datetime.utcnow().isoformat(),
                }}
            )
        
        if subscription_expiry:
            expiry_dt = datetime.datetime.fromisoformat(subscription_expiry)
            now = datetime.datetime.utcnow()
            if now >= expiry_dt:
                if subscription_status == "active":
                    partners_collection.update_one(
                        {"user_id": user_id},
                        {"$set": {"subscription_status": "grace"}}
                    )
                    subscription_status = "grace"
                elif subscription_status == "grace":
                    grace_end = expiry_dt + datetime.timedelta(days=7)
                    if now >= grace_end:
                        partners_collection.update_one(
                            {"user_id": user_id},
                            {"$set": {"subscription_status": "expired"}}
                        )
                        subscription_status = "expired"
        
        return JSONResponse({
            "status": 200,
            "data": {
                "subscription_expiry": subscription_expiry,
                "subscription_status": subscription_status,
            }
        }, status_code=200)
    except Exception as e:
        logger.error(f"Error in /partner-subscription: {e}")
        return JSONResponse({"message": str(e), "status": 500}, status_code=500)

@router.post("/partner-subscription-renew")
async def renew_partner_subscription(data: dict = Depends(get_token)):
    try:
        user_id = data["id"]
        user_data = user_collection.find_one({"_id": user_id})
        
        if not user_data:
            return JSONResponse({"message": "User not found", "status": 404})
        
        settings = get_settings()
        fee = float(settings.get("partner_fee", 100000))
        if user_data.get("wallet_balance", 0) < fee:
            return JSONResponse({"message": "Insufficient balance", "status": 400})
        
        new_expiry = (datetime.datetime.utcnow() + relativedelta(months=int(settings.get("sub_duration_months", 12)))).isoformat()
        
        partners_collection.update_one(
            {"user_id": user_id},
            {"$set": {
                "subscription_expiry": new_expiry,
                "subscription_status": "active",
            }}
        )
        
        user_collection.update_one(
            {"_id": user_id, "wallet_balance": {"$gte": fee}},
            {"$inc": {"wallet_balance": -fee}}
        )
        
        updated_user = user_collection.find_one({"_id": user_id})
        actual_balance = float(updated_user["wallet_balance"])
        
        transactions_collection.insert_one({
            "tx_ref": f"PSUB-{secrets.token_hex(6).upper()}",
            "user_id": user_id,
            "amount": float(fee),
            "gateway": "Wallet",
            "type": "DEBIT",
            "purpose": "Partner Subscription Renewal",
            "status": "SUCCESS",
            "created_at": datetime.datetime.utcnow().isoformat()
        })
        
        return JSONResponse({
            "status": 200,
            "data": {
                "subscription_expiry": new_expiry,
                "subscription_status": "active",
                "wallet_balance": actual_balance,
            }
        }, status_code=200)
    except Exception as e:
        logger.error(f"Error in /partner-subscription-renew: {e}")
        return JSONResponse({"message": str(e), "status": 500}, status_code=500)

@router.post("/toggle-partner-auto-debit")
async def toggle_partner_auto_debit(payload: dict, data: dict = Depends(get_token)):
    try:
        user_id = data.get("id")
        if not user_id:
            return JSONResponse(status_code=401, content={"message": "Invalid user"})
        partner = partners_collection.find_one({"user_id": user_id})
        if not partner:
            return JSONResponse(status_code=404, content={"message": "Partner record not found"})
        enable = payload.get("enable", False)
        pin = payload.get("pin", "")
        if enable:
            if not pin or len(str(pin)) != 4:
                return JSONResponse(status_code=400, content={"message": "Valid 4-digit PIN required"})
            user = user_collection.find_one({"_id": user_id})
            if not user or not user.get("transaction_pin"):
                return JSONResponse(status_code=400, content={"message": "No transaction PIN set"})
            try:
                valid = VerifyHashed(user["transaction_pin"], str(pin))
                if not valid:
                    return JSONResponse(status_code=401, content={"message": "Incorrect PIN"})
            except Exception:
                return JSONResponse(status_code=401, content={"message": "Incorrect PIN"})
            encrypted = _encrypt_pin(str(pin))
            partners_collection.update_one(
                {"user_id": user_id},
                {"$set": {"auto_debit": True, "encrypted_pin": encrypted}}
            )
            return JSONResponse(status_code=200, content={"message": "Auto-renewal enabled", "status": 200})
        else:
            partners_collection.update_one(
                {"user_id": user_id},
                {"$set": {"auto_debit": False, "encrypted_pin": ""}}
            )
            return JSONResponse(status_code=200, content={"message": "Auto-renewal disabled", "status": 200})
    except Exception as e:
        logger.error(f"Toggle partner auto-debit error: {e}")
        return JSONResponse(status_code=500, content={"message": str(e)})

@router.post("/update-partner-photo")
async def update_partner_photo(payload: dict, data: dict = Depends(get_token)):
    try:
        user_id = data["id"]
        photo_base64 = payload.get("photo")
        
        if not photo_base64:
            return JSONResponse({"message": "No photo data provided", "status": 400}, status_code=400)
            
        # Upload to Cloudinary
        photo_url = upload_to_cloudinary(photo_base64, f"partners/photos/{user_id}")
        
        if not photo_url:
            return JSONResponse({"message": "Upload failed", "status": 500}, status_code=500)
            
        # Update database
        partners_collection.update_one(
            {"user_id": user_id},
            {"$set": {"photo": photo_url}}
        )
        
        return JSONResponse({"message": "Photo updated successfully", "url": photo_url, "status": 200}, status_code=200)
    except Exception as e:
        logger.error(f"Error in /update-partner-photo: {e}")
        return JSONResponse({"message": str(e), "status": 500}, status_code=500)

@router.post("/update-partner-details")
async def update_partner_details(payload: dict, data: dict = Depends(get_token)):
    try:
        user_id = data["id"]
        partner_data = partners_collection.find_one({"user_id": user_id})
        if not partner_data:
            return JSONResponse({"message": "Partner record not found", "status": 404}, status_code=404)
        
        allowed_fields = [
            "fullName", "phone", "address", "skills", "profession",
            "accountNumber", "bankName", "description", "workExperience"
        ]
        updates = {}
        for field in allowed_fields:
            if field in payload:
                updates[field] = payload[field]
                
        if updates:
            partners_collection.update_one({"user_id": user_id}, {"$set": updates})
            
        return JSONResponse({"message": "Partner details updated successfully", "status": 200}, status_code=200)
    except Exception as e:
        logger.error(f"Error in /update-partner-details: {e}")
        return JSONResponse({"message": str(e), "status": 500}, status_code=500)


from fastapi.templating import Jinja2Templates

@router.get("/join")
async def join_page(request: Request, ref: str = ""):
    return request.app.state.templates.TemplateResponse("register.html", {"request": request, "ref_code": ref.upper()})

@router.get("/referrals")
async def get_referrals(data: dict = Depends(get_token)):
    user_id = data["id"]
    
    referrals = list(user_collection.find({"referred_by": user_id}, projection={"user_name": 1, "email": 1, "created_at": 1, "is_referral_active": 1}))
    # Convert ObjectIDs to strings
    for ref in referrals:
        ref["_id"] = str(ref["_id"])
        
    return JSONResponse({"status": 200, "data": referrals})


# User regustration otp endpoint
@router.post("/registration_otp")
async def Registration_otp(user:User,background_tasks: BackgroundTasks):
    try:
        user_data=dict(user)
        user_in_db=user_collection.find_one({"email":user_data["email"]})
        if user_in_db:
            return JSONResponse({
            "message":"User already existed with this email",
            "status":status.HTTP_401_UNAUTHORIZED
        })
        
        user_registration_otp=generate_otp()
        user_data["otp"]=user_registration_otp
        background_tasks.add_task(send_email,user_data["email"],user_registration_otp)
        
        return JSONResponse({"message":"otp sent succefully","data":user_data,"status":status.HTTP_200_OK})
    except Exception as e:
        logger.error(f"Error in Registration_otp: {e}")
        return "There's an error, can you check you info"



# User Registration endpoint

@router.post("/register")
async def Register(user:User):
    user_data=dict(user)

    user_in_db=user_collection.find_one({"email":user_data["email"]})
    if user_in_db:
        return JSONResponse({
            "message":"User already existed with this email",
            "status":status.HTTP_401_UNAUTHORIZED
        })
    user_data["password"]=hashedpassword(user_data["password"])
    user_data["otp"]=""
    
    # Generate unique referral code
    user_data["referral_code"] = secrets.token_hex(4).upper()
    
    # Handle Referral Lookup
    referred_by_code = user_data.get("referred_by")
    if referred_by_code:
        referrer = user_collection.find_one({"referral_code": referred_by_code.upper()})
        if referrer:
            user_data["referred_by"] = str(referrer["_id"])
        else:
            user_data["referred_by"] = "" # Invalid code, clear it
    
    user_id=user_collection.insert_one(user_data).inserted_id
    return JSONResponse({"message":"User created successfull","user_id":user_id,"status":status.HTTP_200_OK})



# User login endpoint
@router.post("/login")
async def Login(user:Login):
    user_data=dict(user)
    if user_data["password"] != "":
        user_from_db=user_collection.find_one({"email":user_data["email"]})
        if user_from_db:
            try:
                password_state=VerifyHashed(user_from_db["password"],user_data["password"])
                if password_state:
                    user_id={"id":user_from_db["_id"]}
                    token=create_access_token(user_id)
                    # Return user data (sanitize first)
                    user_info = {
                        "user_name": user_from_db.get("user_name", ""),
                        "email": user_from_db.get("email", ""),
                        "phone_number": user_from_db.get("phone_number", ""),
                        "profile_pic": user_from_db.get("profile_pic", "")
                    }
                    return JSONResponse({"message":"Login Successful","token":token, "user": user_info, "status":status.HTTP_200_OK})
                return JSONResponse({"message":"Incorrect password","status":status.HTTP_401_UNAUTHORIZED})
            except Exception as e:
                logger.error(f"An error occured when trying to authenticate with password: {e}")
                return JSONResponse({"message":"Password seems incorrect","status":status.HTTP_401_UNAUTHORIZED})
        return JSONResponse({"message":"User does not exit","status":status.HTTP_401_UNAUTHORIZED})
        
    # Verifying with otp
    elif user_data["otp"] !="":
        user_from_db=user_collection.find_one({"email":user_data["email"]})
        if user_from_db:
            try:
                # Check expiry
                expiry = user_from_db.get("otp_expiry")
                now = int(time.time())
                if expiry and isinstance(expiry, (int, float)):
                    if now > expiry:
                        return JSONResponse({"message":"OTP has expired","status":status.HTTP_401_UNAUTHORIZED})

                if user_data["otp"]==user_from_db["otp"]:
                    user_id={"id":user_from_db["_id"]}
                    token=create_access_token(user_id)
                    # Return user data (sanitize first)
                    user_info = {
                        "user_name": user_from_db.get("user_name", ""),
                        "email": user_from_db.get("email", ""),
                        "phone_number": user_from_db.get("phone_number", ""),
                        "profile_pic": user_from_db.get("profile_pic", "")
                    }
                    # Reset OTP and rate-limit tracking after successful login
                    user_collection.update_one(
                        {"_id": user_from_db["_id"]}, 
                        {"$set": {"otp": "", "otp_resend_count": 0, "otp_block_until": None}}
                    )
                    return JSONResponse({"message":"Login Successful","token":token, "user": user_info, "status":status.HTTP_200_OK})
                return JSONResponse({"message":"Incorrect OTP","status":status.HTTP_401_UNAUTHORIZED})
            except Exception as e:
                logger.error(f"An error occured when trying to authenticate with OTP: {e}")
                return JSONResponse({"message":"OTP verification failed","status":status.HTTP_401_UNAUTHORIZED})
        return JSONResponse({"message":"User not found","status":status.HTTP_401_UNAUTHORIZED})
        


# Google Auth endpoint
@router.post("/google-auth")
async def google_auth(payload: GoogleAuth):
    token = payload.idToken
    platform = payload.platform
    
    try:
        # The RN Google Sign-In SDK mints the ID token for this project; verify its
        # signature/issuer/expiry. Audience is not enforced here so the token is
        # accepted regardless of which of the project's client IDs minted it.
        logger.info(f"/google-auth platform={platform}")

        # Verify the token
        idinfo = id_token.verify_oauth2_token(token, google_requests.Request(), None)
        
        email = idinfo['email']
        name = idinfo.get('name', '')
        
        # Check if user exists
        user_from_db = user_collection.find_one({"email": email})
        
        if not user_from_db:
            # Create new user
            new_user = {
                "user_name": name,
                "email": email,
                "profile_pic": idinfo.get('picture', ''),
                "phone_number": "",
                "wallet_balance": 0.0,
                "password": "", # No password for Google users
                "otp": "",
                "transaction_pin": "",
                "referral_code": secrets.token_hex(4).upper(),
                "referred_by": "",
                "is_referral_active": False,
                "referral_percentage": 0.0,
                "referral_bonus_paid": False
            }
            
            # Handle Referral Lookup
            referred_by_code = (payload.referred_by or "").strip().upper()
            if referred_by_code:
                referrer = user_collection.find_one({"referral_code": referred_by_code})
                if referrer:
                    new_user["referred_by"] = str(referrer["_id"])
            
            user_id = user_collection.insert_one(new_user).inserted_id
            user_payload = {"id": user_id}
            message = "Registration Successful"
            user_doc = new_user
        else:
            user_payload = {"id": user_from_db["_id"]}
            message = "Login Successful"
            user_doc = user_from_db
            
        # Create JWT token
        token = create_access_token(user_payload)
        
        # Return user data (sanitize first)
        user_info = {
            "user_name": user_doc.get("user_name", ""),
            "email": user_doc.get("email", ""),
            "phone_number": user_doc.get("phone_number", ""),
            "profile_pic": user_doc.get("profile_pic", "")
        }
        
        return JSONResponse({
            "message": message,
            "token": token,
            "user": user_info,
            "status": status.HTTP_200_OK
        })
        
    except ValueError as e:
        logger.error(f"Invalid Google token: {e}")
        return JSONResponse({
            "message": f"Invalid Google token: {e}",
            "status": status.HTTP_401_UNAUTHORIZED
        })
    except Exception as e:
        logger.error(f"Error in /google-auth: {e}")
        return JSONResponse({
            "message": f"Internal server error: {e}",
            "status": status.HTTP_500_INTERNAL_SERVER_ERROR
        })


#Returning user
@router.post("/returning-user")
async def Returning_User():
    pass

# User Forgotten password endpoint
@router.post("/forgotten-password")
async def Forgotten_password(payload: ForgotPassword, background_tasks: BackgroundTasks):
    try:
        email = payload.email
        user = user_collection.find_one({"email": email})
        if not user:
            return JSONResponse({
                "message": "User not found",
                "status": status.HTTP_404_NOT_FOUND
            })
        
        now = int(time.time())
        
        # Check if user is blocked
        block_until = user.get("otp_block_until")
        if block_until and isinstance(block_until, (int, float)):
            if now < block_until:
                remaining_mins = int((block_until - now) / 60)
                if remaining_mins < 1: remaining_mins = 1
                return JSONResponse({
                    "message": f"Too many attempts. Please try again in {remaining_mins} minutes.",
                    "status": 429
                })

        # Track resends
        resend_count = user.get("otp_resend_count", 0)
        
        # If last OTP was sent more than 30 mins ago, reset the count
        last_sent = user.get("otp_last_sent")
        if last_sent and isinstance(last_sent, (int, float)):
            if (now - last_sent) > 1800: # 30 mins
                resend_count = 0

        resend_count += 1
        
        otp = generate_otp()
        expiry = now + 300 # 5 minutes
        
        updates = {
            "otp": otp,
            "otp_expiry": expiry,
            "otp_resend_count": resend_count,
            "otp_last_sent": now
        }

        if resend_count >= 4:
            updates["otp_block_until"] = now + 1800 # 30 minutes
            updates["otp_resend_count"] = 0 # Reset for next session
        else:
            updates["otp_block_until"] = None # Clear block if still within limit

        user_collection.update_one({"email": email}, {"$set": updates})
        
        background_tasks.add_task(send_email, email, otp)
        
        return JSONResponse({
            "message": "OTP sent successfully",
            "status": status.HTTP_200_OK
        })
    except Exception as e:
        logger.error(f"Error in Forgotten_password: {e}")
        return JSONResponse({
            "message": f"Internal server error: {str(e)}",
            "status": status.HTTP_500_INTERNAL_SERVER_ERROR
        })

@router.post("/reset-password")
async def Reset_password(payload: ResetPassword):
    try:
        email = payload.email
        otp = payload.otp
        new_password = payload.new_password
        
        user = user_collection.find_one({"email": email})
        if not user:
            return JSONResponse({
                "message": "User not found",
                "status": status.HTTP_404_NOT_FOUND
            })
            
        if user.get("otp") != otp:
            return JSONResponse({
                "message": "Invalid OTP",
                "status": status.HTTP_401_UNAUTHORIZED
            })
            
        hashed = hashedpassword(new_password)
        user_collection.update_one(
            {"email": email}, 
            {"$set": {"password": hashed, "otp": ""}}
        )
        
        return JSONResponse({
            "message": "Password reset successfully",
            "status": status.HTTP_200_OK
        })
    except Exception as e:
        logger.error(f"Error in Reset_password: {e}")
        return JSONResponse({
            "message": "Internal server error",
            "status": status.HTTP_500_INTERNAL_SERVER_ERROR
        })


# User dashboard endpoint
@router.post("/home")
async def Home(data: dict = Depends(get_token)):
    logger.debug(f"Home access with JWT: {data}")

    try:
        if not data:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"message": "Invalid token"},
            )

        # get user id from token
        user_id = data["id"]

        if not user_id:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"message": "Invalid token payload"},
            )

        # fetch user
        user_data = user_collection.find_one({"_id": user_id})
        if not user_data:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"message": "User not found"},
            )

        # Ensure ID is a string for JSON serialization
        user_data["_id"] = str(user_data["_id"])

        # remove sensitive fields safely
        user_data.pop("password", None)
        user_data.pop("otp", None)
        # Ensure existing users have referral fields
        updates = {}
        if not user_data.get("referral_code"):
            import secrets
            updates["referral_code"] = secrets.token_hex(4).upper()
        if "is_referral_active" not in user_data:
            updates["is_referral_active"] = False
        if "referral_percentage" not in user_data:
            updates["referral_percentage"] = 0.0
        if "partner_type" not in user_data:
            updates["partner_type"] = "partner" if user_data.get("is_partner") else "normal"
            
        if updates:
            user_collection.update_one({"_id": user_id}, {"$set": updates})
            user_data.update(updates)
            
        has_pin = user_data.get("transaction_pin") is not None and user_data.get("transaction_pin") != ""
        user_data.pop("transaction_pin", None)

        # Ensure user_id is a string for the query
        uid_str = str(user_id)

        # Parallelize database queries for better performance
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=5) as executor:
            # 1. Count referrals (Using find + to_list for Astra DB compatibility)
            f_referrals = executor.submit(lambda: list(user_collection.find({"referred_by": uid_str})))
            
            # 2. Fetch credits (scoped to this user for performance)
            f_credits = executor.submit(lambda: list(transactions_collection.find({
                "user_id": uid_str,
                "type": "CREDIT"
            })))
            
            # 3. Fetch debits
            f_debits = executor.submit(lambda: list(transactions_collection.find({
                "user_id": uid_str,
                "type": "DEBIT"
            })))

            # Gather results
            try:
                raw_referrals = f_referrals.result(timeout=8)
                total_referrals = len(raw_referrals)
                
                raw_credits = f_credits.result(timeout=8)
                raw_debits = f_debits.result(timeout=8)
                
                # Manual filtering for user_id to avoid type mismatch issues
                all_credits = [tx for tx in raw_credits if str(tx.get("user_id")) == uid_str]
                all_debits = [tx for tx in raw_debits if str(tx.get("user_id")) == uid_str]
                
                total_earned = sum(
                    float(tx.get("amount", 0)) 
                    for tx in all_credits 
                    if "referral bonus" in str(tx.get("purpose", "")).lower()
                )
                
                # Partner commission earnings
                partner_direct_earned = sum(
                    float(tx.get("amount", 0))
                    for tx in all_credits
                    if tx.get("purpose") == "Partner Direct Commission"
                )
                partner_indirect_earned = sum(
                    float(tx.get("amount", 0))
                    for tx in all_credits
                    if tx.get("purpose") == "Partner Indirect Commission"
                )
                
                # Filter withdrawals for referral-specific ones if any, 
                # or just show total referral bonus withdrawals.
                # If they withdraw from main wallet, we usually track it by purpose.
                total_withdrawn = sum(
                    float(tx.get("amount", 0)) 
                    for tx in all_debits 
                    if "referral" in str(tx.get("purpose", "")).lower()
                )
                
                logger.info(f"Referral stats for {uid_str}: {total_referrals} refs, {total_earned} earned, {total_withdrawn} withdrawn")
            except Exception as ref_err:
                logger.error(f"Error calculating referral stats for {user_id}: {ref_err}")
                total_referrals = 0
                total_earned = 0
                total_withdrawn = 0
                partner_direct_earned = 0
                partner_indirect_earned = 0

        try:
            from routes.withdraw import _compute_earnings
            withdrawable = _compute_earnings(user_id)["available_balance"]
        except Exception as we:
            logger.warning(f"Could not compute withdrawable earnings: {we}")
            withdrawable = 0.0

        user_data["referral_info"] = {
            "code": user_data.get("referral_code", ""),
            "is_active": user_data.get("is_referral_active", False),
            "percentage": user_data.get("referral_percentage", 0.0),
            "total_referrals": total_referrals,
            "total_earned": total_earned,
            "total_withdrawn": total_withdrawn,
            "withdrawable": round(float(withdrawable), 2),
            "partner_type": user_data.get("partner_type", "normal"),
            "partner_direct_earned": partner_direct_earned,
            "partner_indirect_earned": partner_indirect_earned
        }

        is_blocked = user_data.get("status") == "Blocked"
        block_reason = user_data.get("block_reason", "")
        warnings_list = []
        try:
            raw_warnings = list(notifications_collection.find(
                {"user_id": uid_str, "type": "WARNING", "acknowledged": {"$ne": True}}
            ).sort({"created_at": -1}))
            for w in raw_warnings:
                warnings_list.append({
                    "_id": str(w["_id"]),
                    "message": w.get("body", w.get("message", "")),
                    "created_at": w.get("created_at", ""),
                    "acknowledged": w.get("acknowledged", False)
                })
        except Exception as warn_err:
            logger.warning(f"Could not fetch warnings: {warn_err}")

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "message": "user data",
                "data": user_data,
                "has_pin": has_pin,
                "is_blocked": is_blocked,
                "block_reason": block_reason,
                "warnings": warnings_list
            },
        )

    except Exception as e:
        logger.error(f"Error in /home: {e}")

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "message": f"Server error in /home: {str(e)}",
            },
        )




# Transaction PIN Management
@router.post("/set-pin")
async def set_pin(payload: dict, data: dict = Depends(get_token)):
    try:
        if not data:
            raise HTTPException(status_code=401, detail="Invalid token")
        
        user_id = data["id"]
        pin = payload.get("pin")
        
        if not pin or len(str(pin)) != 4:
            raise HTTPException(status_code=400, detail="Invalid PIN format. Must be 4 digits.")
            
        hashed_pin = hashedpassword(str(pin))
        user_collection.update_one({"_id": user_id}, {"$set": {"transaction_pin": hashed_pin}})
        
        return JSONResponse({"message": "Transaction PIN set successfully", "status": 200})
    except Exception as e:
        logger.error(f"Error in /set-pin: {e}")
        return JSONResponse({"message": str(e), "status": 500})

@router.post("/verify-pin")
async def verify_pin(payload: dict, data: dict = Depends(get_token)):
    try:
        if not data:
            raise HTTPException(status_code=401, detail="Invalid token")
            
        user_id = data["id"]
        pin = payload.get("pin")
        
        user_data = user_collection.find_one({"_id": user_id})
        if not user_data or not user_data.get("transaction_pin"):
            return JSONResponse({"message": "No transaction PIN set", "status": 404})
            
        try:
            is_valid = VerifyHashed(user_data["transaction_pin"], str(pin))
            if is_valid:
                return JSONResponse({"message": "PIN verified successfully", "status": 200})
            else:
                return JSONResponse({"message": "Incorrect transaction PIN", "status": 401})
        except Exception:
            return JSONResponse({"message": "Incorrect transaction PIN", "status": 401})
            
    except Exception as e:
        logger.error(f"Error in /verify-pin: {e}")
        return JSONResponse({"message": str(e), "status": 500})

@router.get("/check-pin")
async def check_pin(data: dict = Depends(get_token)):
    try:
        if not data:
            raise HTTPException(status_code=401, detail="Invalid token")
        user_id = data["id"]
        user_data = user_collection.find_one({"_id": user_id})
        has_pin = user_data.get("transaction_pin") is not None and user_data.get("transaction_pin") != ""
        return {"has_pin": has_pin, "status": 200}
    except Exception as e:
        logger.error(f"Error in /check-pin: {e}")
        return JSONResponse({"message": str(e), "status": 500})


@router.post("/send-pin-reset-otp")
async def send_pin_reset_otp(data: dict = Depends(get_token), background_tasks: BackgroundTasks = BackgroundTasks()):
    try:
        user_id = data["id"]
        user = user_collection.find_one({"_id": user_id})
        if not user:
            return JSONResponse({"message": "User not found", "status": 404})
            
        email = user.get("email")
        if not email:
            return JSONResponse({"message": "No email on record", "status": 400})
            
        now = int(time.time())
        
        last_sent = user.get("pin_reset_otp_last_sent")
        if last_sent and isinstance(last_sent, (int, float)):
            if now - last_sent < 30:
                remaining = 30 - (now - last_sent)
                return JSONResponse({
                    "message": f"Please wait {remaining} seconds before requesting another OTP",
                    "status": 429
                })
        
        otp = generate_otp()
        expiry = now + 300
        user_collection.update_one({"_id": user_id}, {"$set": {
            "pin_reset_otp": otp,
            "pin_reset_otp_expiry": expiry,
            "pin_reset_otp_last_sent": now
        }})
        
        background_tasks.add_task(send_email, email, otp)
        
        return JSONResponse({"message": "OTP sent to your email", "status": 200})
    except Exception as e:
        logger.error(f"Error in /send-pin-reset-otp: {e}")
        return JSONResponse({"message": str(e), "status": 500})


@router.post("/verify-pin-reset-otp")
async def verify_pin_reset_otp(payload: dict, data: dict = Depends(get_token)):
    try:
        user_id = data["id"]
        user = user_collection.find_one({"_id": user_id})
        if not user:
            return JSONResponse({"message": "User not found", "status": 404})
            
        otp = str(payload.get("otp", ""))
        if not otp:
            return JSONResponse({"message": "OTP is required", "status": 400})
            
        stored_otp = str(user.get("pin_reset_otp", ""))
        expiry = user.get("pin_reset_otp_expiry")
        
        if not stored_otp or not expiry:
            return JSONResponse({"message": "No OTP requested. Please request a new one.", "status": 400})
            
        now = int(time.time())
        if now > int(expiry):
            return JSONResponse({"message": "OTP has expired. Please request a new one.", "status": 400})
            
        if otp != stored_otp:
            return JSONResponse({"message": "Invalid OTP", "status": 401})
            
        user_collection.update_one({"_id": user_id}, {"$unset": {
            "pin_reset_otp": "",
            "pin_reset_otp_expiry": "",
            "pin_reset_otp_last_sent": ""
        }})
        
        return JSONResponse({"message": "OTP verified successfully", "status": 200})
    except Exception as e:
        logger.error(f"Error in /verify-pin-reset-otp: {e}")
        return JSONResponse({"message": str(e), "status": 500})


# User listing endpoint
@router.post("/listing")
async def Listing():
    house_data=list(house_collection.find({}))
    data = []
    for house in house_data:
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
    
    return JSONResponse({"message":"House data","data":data,"status":status.HTTP_200_OK})


@router.get("/get-selected-house-by-id/{house_id}")
async def get_selected_house_by_id(house_id:str):
    house_data = house_collection.find_one({"_id": house_id})
    if not house_data or "house_image" not in house_data:
        raise HTTPException(status_code=404, detail="House not found")
        
    image_list = house_data["house_image"]
    if not isinstance(image_list, list) or len(image_list) == 0:
        raise HTTPException(status_code=404, detail="Image list is empty")

    first_image_item = image_list[0]
    
    # If it's already a URL, we can redirect or just return the URL logic
    # But for backward compatibility with older app versions that use this as an <img> src:
    if isinstance(first_image_item, str) and not first_image_item.startswith("data:"):
        # It's a file path, return a redirect to the static file
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=get_image_url(first_image_item))

    # Fallback for legacy base64/base85 (same logic as before)
    if isinstance(first_image_item, list):
        image_string = "".join(first_image_item)
    else:
        image_string = first_image_item

    try:
        if image_string.startswith("data:image"):
            base64_data = image_string.split(",", 1)[1]
            image_bytes = base64.b64decode(base64_data)
            media_type = image_string.split(":", 1)[1].split(";", 1)[0]
        else:
            try:
                image_bytes = base64.b85decode(image_string)
                media_type = "image/webp"
            except ValueError:
                image_bytes = base64.b64decode(image_string)
                media_type = "image/jpeg"
    except Exception as e:
        logger.error(f"Error decoding legacy image data for {house_id}: {e}")
        raise HTTPException(status_code=500, detail="Error processing image data")

    return Response(content=image_bytes, media_type=media_type)




# Get selected house details
@router.get("/get-selected-house-details/{house_id}")
async def get_selected_house_details(house_id:str,data: dict = Depends(get_token)):
    try:
        if not data:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"message": "Invalid token"},
            )

        # get user id from token
        user_id = data["id"]

        if not user_id:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"message": "Invalid token payload"},
            )

        # get user
        user_data = user_collection.find_one({"_id": user_id})
        print("user:", user_data)

        if not user_data:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"message": "User not found"},
            )
      
        # fetch house list
        house_data = house_collection.find_one({"_id": house_id})
        if not house_data:
            raise HTTPException(status_code=404, detail="House not found")
        new_image_data=[]
        for i in house_data.get("house_image", []):
            if isinstance(i, list):
                y=reassemble_base64_string(i)
                new_image_data.append(y)
            else:
                new_image_data.append(get_image_url(i))
        house_data["house_image"]=new_image_data

        # house_data.pop("_id")
        return JSONResponse({"message":"House details","data":house_data["house_image"],"wallet":user_data["wallet_balance"],"status":status.HTTP_200_OK})
    
    except Exception as e:
        logger.error(f"Error in /get-selected-house-details: {e}")

        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "message": "token expired or invalid",
            },
        )
       

    





@router.post("/check-user-exists")
async def check_user_exists(data: dict):
    email = data.get("email")
    if not email:
        return JSONResponse({"exists": False, "status": 400})
    user = user_collection.find_one({"email": email})
    if user:
        return JSONResponse({
            "exists": True,
            "user_name": user.get("user_name", ""),
            "profile_pic": user.get("profile_pic", ""),
            "email": user.get("email", ""),
            "status": 200
        })
    return JSONResponse({"exists": False, "status": 200})

# user delete account
@router.post("/delete-account")
async def delete_account(data: dict = Depends(get_token)):
    logger.info(f"Delete account request for user: {data.get('id') if data else 'unknown'}")
    try:
        if not data:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"message": "Invalid token"},
            )

        # get user id from token
        user_id = data.get("id")

        if not user_id:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"message": "Invalid token payload"},
            )

        # delete user
        result = user_collection.delete_one({"_id": str(user_id)})
        
        if result.deleted_count == 0:
            logger.warning(f"Delete failed: User {user_id} not found in database.")
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"message": "User not found"},
            )

        logger.info(f"User {user_id} deleted successfully")

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "message": "user deleted",
                "status": 200
            },
        )


    except Exception as e:
        logger.error(f"Error in /delete-account: {e}")

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "message": f"Server error: {str(e)}",
            },
        )
# User save push token endpoint
@router.post("/save-push-token")
async def save_push_token(payload: dict, data: dict = Depends(get_token)):
    try:
        if not data:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"message": "Invalid token"},
            )

        user_id = data["id"]
        push_token = payload.get("pushToken")

        if not push_token:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"message": "No push token provided"},
            )

        # Update user profile with push token
        user_collection.update_one(
            {"_id": user_id},
            {"$set": {"push_token": push_token}},
            upsert=True
        )

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "message": "Push token registered successfully",
                "status": 200
            },
        )

    except Exception as e:
        logger.error(f"Error in /save-push-token: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "message": f"Server error: {str(e)}",
            },
        )


@router.get("/notification-settings")
async def get_notification_settings(data: dict = Depends(get_token)):
    try:
        if not data:
            return JSONResponse(status_code=401, content={"message": "Invalid token"})
        user_id = data["id"]
        
        user_data = user_collection.find_one({"_id": user_id})
        if not user_data:
            return JSONResponse(status_code=404, content={"message": "User not found"})
            
        settings = user_data.get("notification_settings", {
            "push": False,
            "transactions": True,
            "email": False,
            "sms": False,
            "investments": True,
            "security": True,
            "paymentReminders": False,
            "paymentReminderFrequency": "1 week before"
        })
        
        return JSONResponse(status_code=200, content={"settings": settings})
    except Exception as e:
        logger.error(f"Error in /notification-settings GET: {e}")
        return JSONResponse(status_code=500, content={"message": str(e)})


@router.post("/notification-settings")
async def save_notification_settings(payload: dict, data: dict = Depends(get_token)):
    try:
        if not data:
            return JSONResponse(status_code=401, content={"message": "Invalid token"})
        user_id = data["id"]
        
        new_settings = payload.get("settings")
        if not new_settings:
            return JSONResponse(status_code=400, content={"message": "No settings provided"})
            
        user_collection.update_one(
            {"_id": user_id},
            {"$set": {"notification_settings": new_settings}}
        )
        
        return JSONResponse(status_code=200, content={"message": "Settings updated successfully", "status": 200})
    except Exception as e:
        logger.error(f"Error in /notification-settings POST: {e}")
        return JSONResponse(status_code=500, content={"message": str(e)})

@router.get("/notifications")
async def get_notifications(data: dict = Depends(get_token)):
    try:
        if not data:
            return JSONResponse(status_code=401, content={"message": "Invalid token"})
        user_id = data["id"]
        
        notifications = list(notifications_collection.find({"user_id": user_id}))
        if not notifications:
            # Seed 5 stunning mockup notifications matching their image perfectly!
            now = datetime.datetime.utcnow()
            today_date = now.strftime("%Y-%m-%dT")
            yesterday_date = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%dT")
            
            mockups = [
                {
                    "user_id": user_id,
                    "title": "Wallet Funded (Monnify)",
                    "body": "Your wallet has been credited with ₦100,000.",
                    "type": "PAYMENT",
                    "action_text": "View",
                    "created_at": f"{today_date}12:39:00Z",
                    "is_read": False
                },
                {
                    "user_id": user_id,
                    "title": "Payment Reminder",
                    "body": "Your next payment for Ebenezery Estate is due in 8 days.",
                    "type": "PAYMENT",
                    "action_text": "View",
                    "created_at": f"{today_date}09:40:00Z",
                    "is_read": False
                },
                {
                    "user_id": user_id,
                    "title": "Payment Reminder",
                    "body": "You have an outstanding payment due for your property plan.",
                    "type": "PAYMENT",
                    "action_text": "Pay Now",
                    "created_at": f"{today_date}07:05:00Z",
                    "is_read": True
                },
                {
                    "user_id": user_id,
                    "title": "Payment Failed",
                    "body": "We couldn't process your recent payment. Please try again.",
                    "type": "PAYMENT",
                    "action_text": "View",
                    "created_at": f"{today_date}19:10:00Z",
                    "is_read": True
                },
                {
                    "user_id": user_id,
                    "title": "Contribution Reminder",
                    "body": "Your monthly JardKidz contribution is due tomorrow.",
                    "type": "PAYMENT",
                    "action_text": "View",
                    "created_at": f"{yesterday_date}04:20:00Z",
                    "is_read": False
                }
            ]
            notifications_collection.insert_many(mockups)
            notifications = list(notifications_collection.find({"user_id": user_id}))
            
        # Sort by created_at descending
        notifications.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        
        # Serialize IDs
        for notif in notifications:
            notif["_id"] = str(notif["_id"])
            
        return JSONResponse(
            status_code=200, 
            content={
                "notifications": notifications,
                "data": {
                    "notifications": notifications
                }
            }
        )
    except Exception as e:
        logger.error(f"Error in /notifications GET: {e}")
        return JSONResponse(status_code=500, content={"message": str(e)})


@router.post("/notifications/read-all")
async def read_all_notifications(data: dict = Depends(get_token)):
    try:
        if not data:
            return JSONResponse(status_code=401, content={"message": "Invalid token"})
        user_id = data["id"]
        
        notifications_collection.update_many(
            {"user_id": user_id, "is_read": False},
            {"$set": {"is_read": True}}
        )
        
        return JSONResponse(status_code=200, content={"message": "All notifications marked as read", "status": 200})
    except Exception as e:
        logger.error(f"Error in /notifications/read-all POST: {e}")
        return JSONResponse(status_code=500, content={"message": str(e)})

@router.get("/account-status")
async def get_account_status(data: dict = Depends(get_token)):
    try:
        user_id = data["id"]
        user = user_collection.find_one({"_id": user_id})
        if not user:
            return JSONResponse({"message": "User not found", "status": 404})

        is_blocked = user.get("status") == "Blocked"
        block_reason = user.get("block_reason", "")

        warnings = []
        try:
            raw_warnings = list(notifications_collection.find(
                {"user_id": user_id, "type": "WARNING", "acknowledged": {"$ne": True}}
            ).sort({"created_at": -1}))
            for w in raw_warnings:
                w["_id"] = str(w["_id"])
                warnings.append({
                    "_id": w["_id"],
                    "message": w.get("body", w.get("message", "")),
                    "created_at": w.get("created_at", ""),
                    "acknowledged": w.get("acknowledged", False)
                })
        except Exception as warn_err:
            logger.warning(f"Could not fetch warnings in status: {warn_err}")

        return JSONResponse({
            "data": {
                "is_blocked": is_blocked,
                "block_reason": block_reason,
                "warnings": warnings
            },
            "status": 200
        })
    except Exception as e:
        logger.error(f"Error in /account-status: {e}")
        return JSONResponse({"message": str(e), "status": 500})

@router.post("/acknowledge-warning")
async def acknowledge_warning(payload: dict, data: dict = Depends(get_token)):
    try:
        user_id = data["id"]
        warning_id = payload.get("warning_id")

        if not warning_id:
            return JSONResponse({"message": "Warning ID is required", "status": 400})

        notifications_collection.update_one(
            {"_id": warning_id, "user_id": user_id},
            {"$set": {"acknowledged": True, "acknowledged_at": datetime.datetime.utcnow().isoformat()}}
        )
        return JSONResponse({"message": "Warning acknowledged", "status": 200})
    except Exception as e:
        logger.error(f"Error in /acknowledge-warning: {e}")
        return JSONResponse({"message": str(e), "status": 500})

# user Credit wallet endpoint
@router.post("/credit-wallet")
async def credit_wallet( Amount:dict, background_tasks: BackgroundTasks, data: dict = Depends(get_token)):
    print(Amount["amount"])
    try:
        if not data:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"message": "Invalid token"},
            )

        # get user id from token
        user_id = data["id"]

        if not user_id:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"message": "Invalid token payload"},
            )

        # get user
        user_data = user_collection.find_one({"_id": user_id})
        print("user:", user_data)

        if not user_data:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"message": "User not found"},
            )
      
        # update user wallet
        print(float(user_data["wallet_balance"]+Amount["amount"]))
      
        user_collection.update_one({"_id": user_id}, {"$set": {"wallet_balance": float(user_data["wallet_balance"]+Amount["amount"])}})
        print("user wallet updated")

        # Record Transaction for History
        transactions_collection.insert_one({
            "tx_ref": f"MANUAL-{secrets.token_hex(6).upper()}",
            "user_id": user_id,
            "amount": float(Amount["amount"]),
            "gateway": "Manual/Admin",
            "type": "CREDIT",
            "purpose": "Wallet Funding (Manual)",
            "status": "SUCCESS",
            "created_at": datetime.datetime.utcnow().isoformat()
        })

        # Record Notification
        try:
            notifications_collection.insert_one({
                "user_id": user_id,
                "title": "Wallet Funded (Monnify)",
                "body": f"Your wallet has been credited with ₦{Amount['amount']:,.0f}.",
                "type": "PAYMENT",
                "action_text": "View",
                "created_at": datetime.datetime.utcnow().isoformat(),
                "is_read": False
            })
        except Exception as ne:
            logger.error(f"Error inserting credit notification: {ne}")


        # Send Email
        background_tasks.add_task(
            send_wallet_credit_email,
            user_data["email"],
            user_data.get("user_name", "User"),
            float(Amount["amount"]),
            float(user_data["wallet_balance"] + Amount["amount"])
        )

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "message": "user wallet updated",
            },
        )

    except Exception as e:
        logger.error(f"Error in /credit-wallet: {e}")

        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "message": "token expired or invalid",
            },
        )


# JardKidz Savings Plan Endpoints
@router.post("/create-jard-kidz-plan")
async def create_jard_kidz_plan(plan_data: JardKidzPlan, background_tasks: BackgroundTasks, data: dict = Depends(get_token)):
    try:
        if not data:
            return JSONResponse(status_code=401, content={"message": "Invalid token"})
        
        user_id = data["id"]
        plan = dict(plan_data)
        plan["user_id"] = user_id
        plan["created_at"] = datetime.datetime.utcnow().isoformat()
        
        # 1. Fetch user to check balance
        user_data = user_collection.find_one({"_id": user_id})
        if not user_data:
            return JSONResponse(status_code=404, content={"message": "User not found"})
        
        monthly_payment = float(plan["monthly_amount"])
        if user_data["wallet_balance"] < monthly_payment:
            return JSONResponse(status_code=400, content={"message": "Insufficient balance for first month payment"})
            
        # 2. Deduct first month balance
        new_balance = float(user_data["wallet_balance"] - monthly_payment)
        user_collection.update_one({"_id": user_id}, {"$set": {"wallet_balance": new_balance}})
        
        # 3. Record Transaction
        transactions_collection.insert_one({
            "tx_ref": f"JKDZ-{secrets.token_hex(6).upper()}",
            "user_id": user_id,
            "amount": monthly_payment,
            "gateway": "Wallet",
            "type": "DEBIT",
            "purpose": f"JardKidz Setup: {plan['child_name']}",
            "status": "SUCCESS",
            "created_at": plan["created_at"]
        })
        
        # 4. Add auto-debit & tracking fields
        plan["next_payment_date"] = (datetime.datetime.utcnow() + relativedelta(months=1)).isoformat()
        plan["auto_debit"] = False
        plan["encrypted_pin"] = ""
        plan["penalty_amount"] = 0.0
        plan["reminder_7d_sent"] = False
        plan["reminder_3d_sent"] = False

        # Save Plan
        jard_kidz_collection.insert_one(plan)
        
        # 5. Send Confirmation Email
        background_tasks.add_task(
            send_jard_kidz_email,
            user_data["email"],
            user_data.get("user_name", "User"),
            {
                "child_name": plan["child_name"],
                "plan_type": plan["plan_type"],
                "amount_paid": monthly_payment,
                "months_paid": plan["months_paid"],
                "total_months": plan["total_months"]
            },
            is_setup=True
        )

        return JSONResponse({
            "message": "JardKidz investment plan created successfully",
            "status": 200
        })
        
    except Exception as e:
        logger.error(f"Error in /create-jard-kidz-plan: {e}")
        return JSONResponse(status_code=500, content={"message": str(e)})

@router.post("/get-jard-kidz-plans")
async def get_jard_kidz_plans(data: dict = Depends(get_token)):
    try:
        if not data:
            return JSONResponse(status_code=401, content={"message": "Invalid token"})
        
        user_id = data["id"]
        plans = jard_kidz_collection.find({"user_id": user_id}).to_list()
        
        # Calculate totals
        total_contributed = sum([plan["monthly_amount"] * plan["months_paid"] for plan in plans])
        active_plans = len([p for p in plans if p["status"] == "Active"])
        children_count = len(set([p["child_name"] for p in plans])) # Simplified child count
        
        return JSONResponse({
            "message": "JardKidz plans fetched successfully",
            "data": {
                "plans": plans,
                "total_contributed": total_contributed,
                "active_plans": active_plans,
                "children_count": children_count
            },
            "status": 200
        })
        
    except Exception as e:
        logger.error(f"Error in /get-jard-kidz-plans: {e}")
        return JSONResponse(status_code=500, content={"message": str(e)})

@router.post("/topup-jard-kidz-plan")
async def topup_jard_kidz_plan(payload: dict, background_tasks: BackgroundTasks, data: dict = Depends(get_token)):
    try:
        if not data:
            return JSONResponse(status_code=401, content={"message": "Invalid token"})
        
        user_id = data["id"]
        plan_id = payload.get("plan_id")
        months_to_pay = int(payload.get("months_to_pay", 0))

        if not plan_id or months_to_pay <= 0:
            return JSONResponse(status_code=400, content={"message": "Invalid top-up parameters provided"})

        # Get existing plan
        plan = jard_kidz_collection.find_one({"_id": plan_id})
        if not plan:
            return JSONResponse(status_code=404, content={"message": "Plan not found"})

        if plan["user_id"] != user_id:
             return JSONResponse(status_code=403, content={"message": "Unauthorized access to plan"})

        # Check remaining
        remaining = plan["total_months"] - plan["months_paid"]
        if months_to_pay > remaining:
            return JSONResponse(status_code=400, content={"message": f"Cannot overpay. Only {remaining} months left."})

        penalty = float(plan.get("penalty_amount", 0))
        amount_to_deduct = (months_to_pay * plan["monthly_amount"]) + penalty

        # Check wallet
        user = user_collection.find_one({"_id": user_id})
        if not user or user.get("wallet_balance", 0) < amount_to_deduct:
            return JSONResponse(status_code=400, content={"message": f"Insufficient wallet balance. You need ₦{amount_to_deduct:,.2f} (incl. ₦{penalty:,.2f} penalty) but have ₦{float(user.get('wallet_balance', 0)):,.2f}."})
        
        # Deduct wallet (atomic)
        user_collection.update_one(
            {"_id": user_id, "wallet_balance": {"$gte": amount_to_deduct}},
            {"$inc": {"wallet_balance": -amount_to_deduct}}
        )
        
        updated_user = user_collection.find_one({"_id": user_id})
        actual_balance = float(updated_user["wallet_balance"])

        # Record Transaction
        transaction = {
            "tx_ref": f"JKDZ-TOPUP-{secrets.token_hex(6).upper()}",
            "user_id": user_id,
            "amount": months_to_pay * plan["monthly_amount"],
            "penalty_part": penalty,
            "total_amount": amount_to_deduct,
            "gateway": "Wallet",
            "type": "DEBIT",
            "purpose": f"JardKidz Plan Top-Up: {plan['child_name']} ({months_to_pay} months)",
            "status": "SUCCESS",
            "created_at": datetime.datetime.utcnow().isoformat()
        }
        transactions_collection.insert_one(transaction)

        # Increment Plan Months & reset penalty
        jard_kidz_collection.update_one(
            {"_id": plan_id},
            {"$inc": {"months_paid": months_to_pay},
             "$set": {"penalty_amount": 0}}
        )

        # Send Top-up Email
        background_tasks.add_task(
            send_jard_kidz_email,
            user["email"],
            user.get("user_name", "User"),
            {
                "child_name": plan["child_name"],
                "plan_type": plan["plan_type"],
                "amount_paid": amount_to_deduct,
                "months_paid": plan["months_paid"] + months_to_pay,
                "total_months": plan["total_months"]
            },
            is_setup=False
        )

        return JSONResponse({"message": "Plan top-up successful", "status": 200, "wallet_balance": actual_balance})

    except Exception as e:
        logger.error(f"Error in topup_jard_kidz_plan: {e}")
        return JSONResponse(status_code=500, content={"message": str(e)})


@router.post("/toggle-auto-debit-jardkidz/{plan_id}")
async def toggle_jardkidz_auto_debit(plan_id: str, payload: dict, data: dict = Depends(get_token)):
    try:
        user_id = data.get("id")
        if not user_id:
            return JSONResponse(status_code=401, content={"message": "Invalid user"})
        plan = jard_kidz_collection.find_one({"_id": plan_id})
        if not plan or plan.get("user_id") != user_id:
            return JSONResponse(status_code=404, content={"message": "Plan not found"})
        enable = payload.get("enable", False)
        pin = payload.get("pin", "")
        if enable:
            if not pin or len(str(pin)) != 4:
                return JSONResponse(status_code=400, content={"message": "Valid 4-digit PIN required"})
            user = user_collection.find_one({"_id": user_id})
            if not user or not user.get("transaction_pin"):
                return JSONResponse(status_code=400, content={"message": "No transaction PIN set"})
            try:
                valid = VerifyHashed(user["transaction_pin"], str(pin))
                if not valid:
                    return JSONResponse(status_code=401, content={"message": "Incorrect PIN"})
            except Exception:
                return JSONResponse(status_code=401, content={"message": "Incorrect PIN"})
            if not plan.get("next_payment_date"):
                nxt = (datetime.datetime.utcnow() + relativedelta(months=1)).isoformat()
                jard_kidz_collection.update_one({"_id": plan_id}, {"$set": {"next_payment_date": nxt}})
            encrypted = _encrypt_pin(str(pin))
            jard_kidz_collection.update_one(
                {"_id": plan_id},
                {"$set": {"auto_debit": True, "encrypted_pin": encrypted}}
            )
            return JSONResponse(status_code=200, content={"message": "Auto-debit enabled for JardKidz plan", "status": 200})
        else:
            jard_kidz_collection.update_one(
                {"_id": plan_id},
                {"$set": {"auto_debit": False, "encrypted_pin": ""}}
            )
            return JSONResponse(status_code=200, content={"message": "Auto-debit disabled", "status": 200})
    except Exception as e:
        logger.error(f"Toggle jardkidz auto-debit error: {e}")
        return JSONResponse(status_code=500, content={"message": str(e)})


@router.post("/upload-profile-pic")
async def upload_profile_pic(file: UploadFile = File(...), data: dict = Depends(get_token)):
    user_id = data["id"]
    try:
        # Get old user data to find the current profile pic
        user_data = user_collection.find_one({"_id": user_id})
        if user_data and user_data.get("profile_pic"):
            old_pic = user_data["profile_pic"]
            if "cloudinary" in old_pic:
                # Delete old pic from Cloudinary
                delete_from_cloudinary(old_pic)

        # Read file contents
        contents = await file.read()
        
        # Upload directly to Cloudinary
        pic_url = upload_to_cloudinary(contents, folder="profiles")
        
        if not pic_url:
            return JSONResponse({"message": "Upload to cloud failed", "status": 500})
            
        # Update user record with Cloudinary URL
        user_collection.update_one({"_id": user_id}, {"$set": {"profile_pic": pic_url}})
        
        return JSONResponse({"message": "Profile picture updated", "status": 200, "url": pic_url})
    except Exception as e:
        logger.error(f"Upload Error: {e}")
        return JSONResponse({"message": f"Upload failed: {str(e)}", "status": 500})


# Profile Update endpoint
@router.post("/update-profile")
async def update_profile(payload: dict, data: dict = Depends(get_token)):
    try:
        user_id = data["id"]
        updates = {}
        if "user_name" in payload:
            updates["user_name"] = payload["user_name"]
        if "phone_number" in payload:
            updates["phone_number"] = payload["phone_number"]
            
        if not updates:
            return JSONResponse({"message": "No updates provided", "status": 400})
            
        user_collection.update_one({"_id": user_id}, {"$set": updates})
        return JSONResponse({"message": "Profile updated successfully", "status": 200})
    except Exception as e:
        logger.error(f"Error in update_profile: {e}")
        return JSONResponse({"message": str(e), "status": 500})


# Bank Account Management
@router.get("/get-bank-accounts")
async def get_bank_accounts(data: dict = Depends(get_token)):
    try:
        user_id = data["id"]
        user = user_collection.find_one({"_id": user_id})
        if not user:
            return JSONResponse({"message": "User not found", "status": 404})
        
        bank_accounts = user.get("bank_accounts", [])
        return JSONResponse({"data": bank_accounts, "status": 200})
    except Exception as e:
        logger.error(f"Error in get_bank_accounts: {e}")
        return JSONResponse({"message": str(e), "status": 500})

@router.post("/add-bank-account")
async def add_bank_account(bank: dict, data: dict = Depends(get_token)):
    try:
        user_id = data["id"]
        # Generate a simple unique ID for the bank entry if not present
        if "id" not in bank:
            bank["id"] = secrets.token_hex(4)
            
        user_collection.update_one(
            {"_id": user_id},
            {"$push": {"bank_accounts": bank}}
        )
        return JSONResponse({"message": "Bank account linked successfully", "status": 200})
    except Exception as e:
        logger.error(f"Error in add_bank_account: {e}")
        return JSONResponse({"message": str(e), "status": 500})

@router.delete("/delete-bank-account/{bank_id}")
async def delete_bank_account(bank_id: str, data: dict = Depends(get_token)):
    try:
        user_id = data["id"]
        user_collection.update_one(
            {"_id": user_id},
            {"$pull": {"bank_accounts": {"id": bank_id}}}
        )
        return JSONResponse({"message": "Bank account removed successfully", "status": 200})
    except Exception as e:
        logger.error(f"Error in delete_bank_account: {e}")
        return JSONResponse({"message": str(e), "status": 500})

@router.put("/update-bank-account/{bank_id}")
async def update_bank_account(bank_id: str, updated_bank: dict, data: dict = Depends(get_token)):
    try:
        user_id = data["id"]
        # Remove the bank with this ID and push the updated one
        # In MongoDB/AstraDB JSON, we can use positional operator or pull/push
        user_collection.update_one(
            {"_id": user_id},
            {"$pull": {"bank_accounts": {"id": bank_id}}}
        )
        # Ensure ID stays the same
        updated_bank["id"] = bank_id
        user_collection.update_one(
            {"_id": user_id},
            {"$push": {"bank_accounts": updated_bank}}
        )
        return JSONResponse({"message": "Bank account updated successfully", "status": 200})
    except Exception as e:
        logger.error(f"Error in update_bank_account: {e}")
        return JSONResponse({"message": str(e), "status": 500})
@router.post("/jardhouz-register")
async def register_jardhouz_membership(details: dict, data: dict = Depends(get_token)):
    try:
        user_id = data.get("id")
        user = user_collection.find_one({"_id": user_id})
        
        if not user:
            return JSONResponse({"message": "User not found", "status": 404})
            
        if user.get("is_jardhouz_registered"):
            return JSONResponse({"message": "Already a JardHouz member", "status": 400})
            
        # Check balance
        entrance_fee = 100000.0
        current_balance = float(user.get("wallet_balance", 0))
        
        if current_balance < entrance_fee:
            return JSONResponse({"message": "Insufficient balance for entrance fee", "status": 400})
            
        # Deduct fee
        new_balance = current_balance - entrance_fee
        
        # Generate JH-ID
        import random
        jh_id = f"JH-{random.randint(1000, 9999)}"
        
        # Update user record
        update_data = {
            "wallet_balance": new_balance,
            "is_jardhouz_registered": True,
            "jardhouz_id": jh_id,
            "jardhouz_details": {
                "full_name": details.get("fullName"),
                "email": details.get("email"),
                "phone": details.get("phoneNumber"),
                "address": details.get("address"),
                "age": details.get("age"),
                "source_of_income": details.get("sourceOfIncome"),
                "registered_at": datetime.datetime.utcnow().isoformat()
            }
        }
        
        user_collection.update_one({"_id": user_id}, {"$set": update_data})
        
        # Record transaction
        transactions_collection.insert_one({
            "tx_ref": f"JH-REG-{secrets.token_hex(6).upper()}",
            "user_id": user_id,
            "amount": entrance_fee,
            "gateway": "Wallet",
            "type": "DEBIT",
            "purpose": "JardHouz Entrance Fee",
            "status": "SUCCESS",
            "created_at": datetime.datetime.utcnow().isoformat()
        })
        
        logger.info(f"User {user_id} registered for JardHouz. ID: {jh_id}")
        return JSONResponse({"message": "Registration successful", "jardhouz_id": jh_id, "status": 200})
        
    except Exception as e:
        logger.error(f"Error in jardhouz-register: {e}")
        return JSONResponse({"message": str(e), "status": 500})
