from fastapi import APIRouter,Depends,HTTPException,status,BackgroundTasks, Request, UploadFile, File
from db.database import user_collection, house_collection, transactions_collection, jard_kidz_collection, vendors_collection
import datetime
import secrets
from utill import hashedpassword,VerifyHashed,create_access_token,get_token,process_base85_image,reassemble_base64_string, send_jard_kidz_email, send_wallet_credit_email, get_image_url, upload_to_cloudinary, delete_from_cloudinary

from fastapi.responses import JSONResponse, Response
from utill import generate_otp,get_token,send_email
import base64
from starlette import status
import logging
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
import os
from model import User, Login, GoogleAuth, JardKidzPlan, ForgotPassword, ResetPassword, VendorRegister
import shutil
import time

logger = logging.getLogger("jardx")

router = APIRouter(prefix="/users", tags=["Users"])

@router.post("/vendor-register")
async def vendor_register(vendor: VendorRegister, data: dict = Depends(get_token)):
    try:
        user_id = data["id"]
        user_data = user_collection.find_one({"_id": user_id})
        
        if not user_data:
            return JSONResponse({"message": "User not found", "status": 404})
            
        # Check balance
        if user_data.get("wallet_balance", 0) < 25000:
            return JSONResponse({"message": "Insufficient balance", "status": 400})
            
        # Upload images to Cloudinary
        photo_url = ""
        cert_url = ""
        
        if vendor.photo:
            photo_url = upload_to_cloudinary(vendor.photo, f"vendors/photos/{user_id}")
            
        if vendor.certificate:
            cert_url = upload_to_cloudinary(vendor.certificate, f"vendors/certs/{user_id}")
            
        # Prepare vendor record
        vendor_record = vendor.dict()
        vendor_record["user_id"] = user_id
        vendor_record["photo"] = photo_url
        vendor_record["certificate"] = cert_url
        vendor_record["status"] = "unverified"
        vendor_record["created_at"] = datetime.datetime.utcnow().isoformat()
        
        # Save to database
        vendors_collection.insert_one(vendor_record)
        
        # Deduct fee
        new_balance = float(user_data["wallet_balance"]) - 25000
        user_collection.update_one({"_id": user_id}, {"$set": {"wallet_balance": new_balance}})
        
        # Record transaction
        transactions_collection.insert_one({
            "tx_ref": f"VNDR-{secrets.token_hex(6).upper()}",
            "user_id": user_id,
            "amount": 25000.0,
            "type": "DEBIT",
            "purpose": "Vendor Registration Fee",
            "status": "SUCCESS",
            "created_at": datetime.datetime.utcnow().isoformat()
        })
        
        return JSONResponse({"message": "Registration successful", "status": 200})
        
    except Exception as e:
        logger.error(f"Error in /vendor-register: {e}")
        return JSONResponse({"message": str(e), "status": 500})

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
        # Determine the correct client ID
        if platform == "android":
            client_id = os.getenv("GOOGLE_ANDROID_CLIENT_ID")
        elif platform == "ios":
            client_id = os.getenv("GOOGLE_IOS_CLIENT_ID")
        else:
            client_id = os.getenv("GOOGLE_WEB_CLIENT_ID")
            
        # Verify the token
        idinfo = id_token.verify_oauth2_token(token, google_requests.Request(), client_id)
        
        email = idinfo['email']
        name = idinfo.get('name', '')
        
        # Check if user exists
        user_from_db = user_collection.find_one({"email": email})
        
        if not user_from_db:
            # Create new user
            new_user = {
                "user_name": name,
                "email": email,
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
            user_id = user_collection.insert_one(new_user).inserted_id
            user_payload = {"id": user_id}
            message = "Registration Successful"
        else:
            user_payload = {"id": user_from_db["_id"]}
            message = "Login Successful"
            
        # Create JWT token
        token = create_access_token(user_payload)
        
        return JSONResponse({
            "message": message,
            "token": token,
            "status": status.HTTP_200_OK
        })
        
    except ValueError as e:
        logger.error(f"Invalid Google token: {e}")
        return JSONResponse({
            "message": "Invalid Google token",
            "status": status.HTTP_401_UNAUTHORIZED
        })
    except Exception as e:
        logger.error(f"Error in /google-auth: {e}")
        return JSONResponse({
            "message": "Internal server error",
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
        expiry = now + 60 # 1 minute
        
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
                status_code=status.HTTP_404_NOT_FOUND,
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
            
            # 2. Fetch credits (Broadened for diagnostics)
            f_credits = executor.submit(lambda: list(transactions_collection.find({
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

        user_data["referral_info"] = {
            "code": user_data.get("referral_code", ""),
            "is_active": user_data.get("is_referral_active", False),
            "percentage": user_data.get("referral_percentage", 0.0),
            "total_referrals": total_referrals,
            "total_earned": total_earned,
            "total_withdrawn": total_withdrawn
        }

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "message": "user data",
                "data": user_data,
                "has_pin": has_pin
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
        
        # 4. Save Plan
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

        amount_to_deduct = months_to_pay * plan["monthly_amount"]

        # Check wallet
        user = user_collection.find_one({"_id": user_id})
        if not user or user.get("wallet_balance", 0) < amount_to_deduct:
            return JSONResponse(status_code=400, content={"message": "Insufficient wallet balance."})
        
        # Deduct wallet
        new_balance = float(user["wallet_balance"]) - amount_to_deduct
        user_collection.update_one({"_id": user_id}, {"$set": {"wallet_balance": new_balance}})

        # Record Transaction
        transaction = {
            "tx_ref": f"JKDZ-TOPUP-{secrets.token_hex(6).upper()}",
            "user_id": user_id,
            "amount": amount_to_deduct,
            "gateway": "Wallet",
            "type": "DEBIT",
            "purpose": f"JardKidz Plan Top-Up: {plan['child_name']} ({months_to_pay} months)",
            "status": "SUCCESS",
            "created_at": datetime.datetime.now(datetime.timezone.utc)
        }
        transactions_collection.insert_one(transaction)

        # Increment Plan Months
        jard_kidz_collection.update_one(
            {"_id": plan_id},
            {"$inc": {"months_paid": months_to_pay}}
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

        return JSONResponse({"message": "Plan top-up successful", "status": 200})

    except Exception as e:
        logger.error(f"Error in topup_jard_kidz_plan: {e}")
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
