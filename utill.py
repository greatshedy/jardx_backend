from argon2 import PasswordHasher
import random
from dotenv import load_dotenv
import os
import resend
import secrets
from jose import JWTError, jwt,ExpiredSignatureError
from datetime import datetime, timedelta
from fastapi import status,HTTPException, BackgroundTasks
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import Depends
import base64
import io
from PIL import Image
from fastapi.responses import StreamingResponse
import cloudinary
import cloudinary.uploader
import logging

logger = logging.getLogger("jardx")



# Use absolute path to ensure .env is loaded regardless of current working directory
current_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(current_dir, ".env"))

resend.api_key = os.getenv("RESEND_API_KEY")

ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "fallback_secret_keep_this_secure")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 60))
BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://10.12.228.168:8000")

# Cloudinary Setup
cloudinary_url = os.getenv("CLOUDINARY_URL")
if cloudinary_url:
    try:
        # cloudinary://api_key:api_secret@cloud_name
        parts = cloudinary_url.replace("cloudinary://", "").replace("\"", "").replace("'", "")
        auth, cloud_name = parts.split("@")
        api_key, api_secret = auth.split(":")
        
        cloudinary.config(
            cloud_name=cloud_name,
            api_key=api_key,
            api_secret=api_secret,
            secure=True
        )
    except Exception as e:
        logger.error(f"Error parsing CLOUDINARY_URL: {e}")

def get_image_url(image_path: str) -> str:
    """
    Converts a relative image path (/uploads/...) to a full URL.
    If the input is already a URL or base64, returns it as is.
    Now supports Cloudinary URLs as well.
    """
    if not image_path:
        return ""
    if image_path.startswith("http") or image_path.startswith("data:") or "cloudinary" in image_path:
        return image_path
    
    return f"{BACKEND_BASE_URL.rstrip('/')}/{image_path.lstrip('/')}"

def upload_to_cloudinary(image_data: str, folder="jardx_uploads") -> str:
    """
    Uploads an image to Cloudinary and returns the secure URL.
    Supports base64 strings, file paths, or public URLs.
    """
    try:
        if not image_data:
            return ""
        
        upload_result = cloudinary.uploader.upload(
            image_data, 
            folder=folder,
            resource_type="auto"
        )
        return upload_result.get("secure_url", "")
    except Exception as e:
        print(f"ERROR: Cloudinary upload failed: {e}")
        return ""

def delete_from_cloudinary(url: str):
    """
    Deletes an image from Cloudinary given its secure URL.
    """
    try:
        if not url or "cloudinary" not in url:
            return
        
        # Extract public_id from URL
        # Example: https://res.cloudinary.com/cloud_name/image/upload/v12345/folder/public_id.jpg
        # We need "folder/public_id"
        
        # Split by /upload/
        if "/upload/" in url:
            parts = url.split("/upload/")[1]
            # Remove versioning (v12345/) if present
            if parts.startswith("v"):
                parts = "/".join(parts.split("/")[1:])
            
            # Remove file extension (.jpg, .png, etc.)
            public_id = os.path.splitext(parts)[0]
            
            cloudinary.uploader.destroy(public_id)
            print(f"DEBUG: Cloudinary image deleted: {public_id}")
    except Exception as e:
        print(f"ERROR: Cloudinary deletion failed: {e}")


def generate_otp(length=4):
    otp = ''.join([str(random.randint(0, 9)) for _ in range(length)])
    return otp

# # Example usage
# otp = generate_otp()
# print("Your OTP is:", otp)

ph=PasswordHasher()
def hashedpassword(password):
    hashed=ph.hash(password)
    return hashed


def VerifyHashed(hashedpassword,password):
    value=ph.verify(hashedpassword,password)
    return value




def send_email(receiver_email, otp):
    sender = os.getenv("RESEND_SENDER", "onboarding@resend.dev")

    html_content = f"""
    <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; background-color: #f4f4f4; color: #333; text-align: center; padding: 20px; }}
                .container {{ background-color: #ffffff; padding: 30px; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); display: inline-block; }}
                h1 {{ color: #ff6900; }}
                .otp {{ font-size: 32px; font-weight: bold; color: #ff6900; margin: 20px 0; }}
                p {{ font-size: 16px; }}
                .footer {{ margin-top: 30px; font-size: 12px; color: #888; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>JardX OTP Verification</h1>
                <p>Your One-Time Password (OTP) is:</p>
                <div class="otp">{otp}</div>
                <p>This OTP is valid for 5 minutes only.</p>
                <p>If you did not request this code, please ignore this email.</p>
                <div class="footer">© {datetime.now().year} JardX. All rights reserved.</div>
            </div>
        </body>
    </html>
    """

    try:
        resend.Emails.send({
            "from": sender,
            "to": [receiver_email],
            "subject": "Your OTP Code for JardX",
            "html": html_content
        })
        print("SUCCESS: OTP Email sent successfully!")
        return otp
    except Exception as e:
        print("ERROR sending OTP email:", e)
        return "Email sending failed"


# send_email("greatboyshedy@gmail.com",1234)


# create jwt
# Secret key (keep this safe, usually from environment variables)
 # algorithm to encode the JWT
def create_access_token(data: dict, expires_delta: int = None):
    if expires_delta is None:
        expires_delta = ACCESS_TOKEN_EXPIRE_MINUTES
    """
    Create a JWT access token.
    
    Args:
        data (dict): The payload data you want to include in the token (e.g., user_id, email)
        expires_delta (int, optional): Expiration time in minutes. Defaults to 60.
    
    Returns:
        str: Encoded JWT token
    """
    try:
        # Copy data to avoid modifying original dict
        to_encode = data.copy()
        
        # Set the expiration time
        expire = datetime.utcnow() + timedelta(minutes=expires_delta)
        to_encode.update({"exp": expire})
        
        # Encode the token
        encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
        return encoded_jwt
    
    except JWTError as e:
        # Handle JWT errors (rare during creation)
        print("Error creating JWT:", str(e))
        return None
    except Exception as e:
        # Handle unexpected errors
        print("Unexpected error creating JWT:", str(e))
        return None




def decode_access_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload  # ✅ valid token

    except ExpiredSignatureError:
        print("❌ Token expired")

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
        )

    except JWTError:
        print("❌ Invalid token")

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )  # invalid token
    

# This handles "Authorization: Bearer <token>"
security = HTTPBearer()

def get_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Extract, decode and validate JWT token
    """

    try:
        token = credentials.credentials

        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token not provided",
            )

        # 🔐 Decode token
        payload = decode_access_token(token)

        

        return payload  # 👈 return decoded data instead of raw token

    except Exception as e:
        print("❌ Unexpected Error:", str(e))

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed",
        )


# this is for chunking base64 string so that it can be stored in astra db

def chunk_base64_string(b64_string, chunk_size=4000):  # 4000 bytes < 8 KB
    return [b64_string[i:i + chunk_size] for i in range(0, len(b64_string), chunk_size)]


# this is for reassembling the chunked base64 string

def reassemble_base64_string(chunked_b64_list):
    return "".join(chunked_b64_list)






def process_base85_image(base64_string: str, size=(500, 500), quality=70) -> str:
    """
    Same as above but uses base85 encoding (smaller size)
    """
    # Remove HTML Data URI prefix if it exists
    if "," in base64_string:
        base64_string = base64_string.split(",", 1)[1]
        
    image_bytes = base64.b64decode(base64_string)
    img = Image.open(io.BytesIO(image_bytes))
    img.thumbnail(size)

    buffer = io.BytesIO()
    img.save(buffer, format="WEBP", quality=quality)

    optimized_bytes = buffer.getvalue()

    return base64.b85encode(optimized_bytes).decode("utf-8")

def send_purchase_email(receiver_email, user_name, house_name, plan_type, amount_paid, remaining_balance, image_data):
    sender = os.getenv("RESEND_SENDER", "onboarding@resend.dev")

    is_url = image_data and str(image_data).startswith("http")

    html_content = f"""
    <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; background-color: #f4f4f4; color: #333; text-align: center; padding: 20px; }}
                .container {{ background-color: #ffffff; padding: 30px; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); display: inline-block; max-width: 600px; width: 100%; }}
                h1 {{ color: #ff6900; }}
                .details {{ text-align: left; background: #F6EEE9; padding: 20px; border-radius: 10px; margin: 20px 0; font-size: 16px; border-left: 4px solid #ff6900; }}
                .img-container {{ margin-top: 20px; }}
                img {{ max-width: 100%; border-radius: 10px; }}
                .footer {{ margin-top: 30px; font-size: 12px; color: #888; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Transaction Successful!</h1>
                <p>Hello {user_name},</p>
                <p>Congratulations! We successfully processed your real estate transaction with JardX.</p>
                
                <div class="details">
                    <p><strong>Property:</strong> {house_name}</p>
                    <p><strong>Transaction Type:</strong> {plan_type.title()}</p>
                    <p><strong>Amount Paid:</strong> ₦{amount_paid:,.2f}</p>
                    <p><strong>Remaining Balance:</strong> ₦{remaining_balance:,.2f}</p>
                </div>
    """

    if image_data:
        img_src = image_data if is_url else image_data
        html_content += f"""
                <div class="img-container">
                    <img src="{img_src}" alt="Property Image" />
                </div>
        """

    html_content += f"""
                <p>Thank you for trusting JardX. Visit your portfolio dashboard to manage your assets.</p>
                <div class="footer">© {datetime.now().year} JardX. All rights reserved.</div>
            </div>
        </body>
    </html>
    """

    attachments = []
    if image_data and not is_url:
        try:
            if image_data.startswith("data:image"):
                base64_data = image_data.split(",", 1)[1]
            else:
                try:
                    base64_data = base64.b64encode(base64.b85decode(image_data)).decode("utf-8")
                except Exception:
                    base64_data = image_data
            attachments.append({
                "filename": "property.jpg",
                "content": base64_data,
                "content_id": "house_image"
            })
            html_content = html_content.replace(
                '<img src="{image_data}"', '<img src="cid:house_image"'
            )
        except Exception as e:
            print("Could not attach image to email:", e)

    params = {
        "from": sender,
        "to": [receiver_email],
        "subject": f"Confirmation: {house_name} ({plan_type.title()})",
        "html": html_content
    }
    if attachments:
        params["attachments"] = attachments

    try:
        resend.Emails.send(params)
        print(f"SUCCESS: Purchase Email sent successfully to {receiver_email}")
    except Exception as e:
        print("ERROR sending purchase email:", e)

def send_jard_kidz_email(receiver_email, user_name, plan_details, is_setup=True):
    sender = os.getenv("RESEND_SENDER", "onboarding@resend.dev")

    child_name = plan_details.get("child_name", "your child")
    plan_type_raw = plan_details.get("plan_type", "child_savings")
    plan_type = "Property Plan" if plan_type_raw == "child_property" else "Savings Plan"
    sub_type = "property" if plan_type_raw == "child_property" else "savings"
    amount = float(plan_details.get("amount_paid", 0))
    months_paid = plan_details.get("months_paid", 1)
    total_months = plan_details.get("total_months", 0)

    subject = f"Confirmation: JardKidz {plan_type} Setup" if is_setup else f"Receipt: JardKidz {plan_type} Top-up"

    html_content = f"""
    <html>
        <head>
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f9f9f9; color: #444; margin: 0; padding: 0; }}
                .container {{ max-width: 600px; margin: 20px auto; background-color: #ffffff; padding: 40px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); }}
                .header {{ text-align: center; margin-bottom: 30px; }}
                h1 {{ color: #ff6900; margin: 0; font-size: 24px; }}
                .status-badge {{ display: inline-block; padding: 6px 12px; background-color: #fff0e6; color: #ff6900; border-radius: 20px; font-weight: bold; font-size: 14px; margin-top: 10px; }}
                .content {{ line-height: 1.6; font-size: 16px; color: #555; }}
                .details-box {{ background-color: #fcf6f2; border-left: 4px solid #ff6900; padding: 20px; border-radius: 8px; margin: 25px 0; }}
                .details-row {{ display: flex; justify-content: space-between; margin-bottom: 10px; border-bottom: 1px dashed #eee; padding-bottom: 5px; }}
                .details-label {{ font-weight: bold; color: #777; }}
                .details-value {{ color: #333; font-weight: 500; }}
                .footer {{ text-align: center; margin-top: 40px; font-size: 13px; color: #aaa; border-top: 1px solid #eee; padding-top: 20px; }}
                .cta {{ display: block; width: 200px; margin: 30px auto; background-color: #ff6900; color: #ffffff; text-decoration: none; padding: 12px; border-radius: 8px; text-align: center; font-weight: bold; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>JardKidz {plan_type}</h1>
                    <div class="status-badge">{"INVESTMENT ACTIVE" if is_setup else "PAYMENT SUCCESSFUL"}</div>
                </div>
                <div class="content">
                    <p>Hello <strong>{user_name}</strong>,</p>
                    <p>{"Your child's future investment has been successfully initialized" if is_setup else "We've received your installment payment for your child's investment"}. Here are the transaction details:</p>

                    <div class="details-box">
                        <div class="details-row">
                            <span class="details-label">Child's Name:</span>
                            <span class="details-value">{child_name}</span>
                        </div>
                        <div class="details-row">
                            <span class="details-label">Amount Paid:</span>
                            <span class="details-value">₦{amount:,.2f}</span>
                        </div>
                        <div class="details-row">
                            <span class="details-label">Installments:</span>
                            <span class="details-value">{months_paid} of {total_months} months</span>
                        </div>
                        <div class="details-row">
                            <span class="details-label">Plan Type:</span>
                            <span class="details-value">JardKidz {plan_type}</span>
                        </div>
                    </div>

                    <p>Thank you for choosing <strong>JardX</strong> to secure your child's future. You can track this progress in real-time on your dashboard.</p>
                </div>
                <a href="{os.getenv('MOBILE_URL', '#')}?autoOpen=true&type=kidz&sub_type={sub_type}&name={child_name}" class="cta">Open in JardX App</a>
                <p style="text-align: center; margin-top: 10px;">
                    <a href="{os.getenv('MOBILE_URL', '#')}?type=kidz&sub_type={sub_type}&name={child_name}" style="color: #ff6900; font-size: 14px; text-decoration: none;">View in Browser</a>
                </p>
                <div class="footer">
                    <p>© {datetime.now().year} JardX Technologies. All rights reserved.</p>
                    <p>This is an automated receipt. Please do not reply to this email.</p>
                </div>
            </div>
        </body>
    </html>
    """

    try:
        resend.Emails.send({
            "from": sender,
            "to": [receiver_email],
            "subject": subject,
            "html": html_content
        })
        print(f"SUCCESS: JardKidz Email sent to {receiver_email}")
    except Exception as e:
        print("ERROR sending JardKidz email:", e)

def send_wallet_credit_email(receiver_email, user_name, amount, new_balance):
    sender = os.getenv("RESEND_SENDER", "onboarding@resend.dev")

    html_content = f"""
    <html>
        <head>
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f7f9fc; color: #333; margin: 0; padding: 0; }}
                .container {{ max-width: 600px; margin: 30px auto; background-color: #ffffff; padding: 40px; border-radius: 15px; border-top: 6px solid #4CAF50; box-shadow: 0 5px 20px rgba(0,0,0,0.05); }}
                .header {{ text-align: center; margin-bottom: 30px; }}
                h1 {{ color: #4CAF50; margin: 0; font-size: 26px; }}
                .amount-display {{ text-align: center; font-size: 36px; font-weight: bold; color: #333; margin: 20px 0; }}
                .content {{ line-height: 1.6; font-size: 16px; color: #666; }}
                .balance-info {{ background-color: #f1f8e9; padding: 20px; border-radius: 10px; text-align: center; margin: 25px 0; }}
                .balance-label {{ color: #558b2f; font-size: 14px; text-transform: uppercase; letter-spacing: 1px; font-weight: bold; }}
                .balance-value {{ font-size: 24px; color: #333; margin-top: 5px; font-weight: bold; }}
                .footer {{ text-align: center; margin-top: 40px; font-size: 13px; color: #999; border-top: 1px solid #f0f0f0; padding-top: 20px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>Wallet Credited!</h1>
                    <p>Your transaction has been processed successfully.</p>
                </div>
                <div class="amount-display">₦{amount:,.2f}</div>
                <div class="content">
                    <p>Hello <strong>{user_name}</strong>,</p>
                    <p>Your JardX wallet has been successfully credited. Your funds are now available for investment in property or JardKidz plans.</p>

                    <div class="balance-info">
                        <div class="balance-label">Current Wallet Balance</div>
                        <div class="balance-value">₦{new_balance:,.2f}</div>
                    </div>
                </div>
                <a href="{os.getenv('MOBILE_URL', '#')}?autoOpen=true&type=payment&amount={amount:.2f}" style="display: block; width: 220px; margin: 30px auto; background-color: #4CAF50; color: #ffffff; text-decoration: none; padding: 14px; border-radius: 10px; text-align: center; font-weight: bold;">Open JardX App</a>
                <p style="text-align: center; margin-top: 10px;">
                    <a href="{os.getenv('MOBILE_URL', '#')}?type=payment&amount={amount:.2f}" style="color: #4CAF50; font-size: 14px; text-decoration: none;">View in Browser</a>
                </p>
                <div class="footer">
                    <p>© {datetime.now().year} JardX Technologies. All rights reserved.</p>
                    <p>Thank you for choosing JardX for your real estate investments.</p>
                </div>
            </div>
        </body>
    </html>
    """

    try:
        resend.Emails.send({
            "from": sender,
            "to": [receiver_email],
            "subject": "Wallet Credit Confirmation - JardX",
            "html": html_content
        })
        print(f"SUCCESS: Wallet Credit Email sent to {receiver_email}")
    except Exception as e:
        print("ERROR sending wallet credit email:", e)


def send_account_deletion_request_email(admin_emails, user_name, user_email, reason, request_id):
    sender = os.getenv("RESEND_SENDER", "onboarding@resend.dev")
    recipients = [e.strip() for e in (admin_emails or []) if e and e.strip()]
    if not recipients:
        return

    requested_at = datetime.now().strftime("%d %b %Y, %I:%M %p")
    reason_display = reason.strip() if reason and reason.strip() else "No reason provided"

    html_content = f"""
    <html>
        <head>
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f4f4; color: #333; margin: 0; padding: 20px; }}
                .container {{ max-width: 600px; margin: 0 auto; background-color: #ffffff; padding: 36px; border-radius: 12px; border-top: 6px solid #DC2626; box-shadow: 0 4px 15px rgba(0,0,0,0.06); }}
                h1 {{ color: #DC2626; margin: 0 0 8px 0; font-size: 22px; }}
                .badge {{ display: inline-block; padding: 6px 14px; background-color: #FEE2E2; color: #B91C1C; border-radius: 20px; font-weight: bold; font-size: 12px; letter-spacing: .5px; text-transform: uppercase; }}
                .content {{ line-height: 1.6; font-size: 15px; color: #555; margin-top: 20px; }}
                .details {{ background-color: #FEF2F2; border-left: 4px solid #DC2626; padding: 18px 20px; border-radius: 8px; margin: 22px 0; }}
                .row {{ padding: 5px 0; }}
                .label {{ color: #777; font-weight: bold; }}
                .value {{ color: #222; }}
                .footer {{ text-align: center; margin-top: 32px; font-size: 12px; color: #aaa; border-top: 1px solid #eee; padding-top: 18px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Account Deletion Request</h1>
                <span class="badge">Action Required</span>
                <div class="content">
                    <p>A JardX user has verified their email and requested that their account be deleted.</p>
                    <div class="details">
                        <div class="row"><span class="label">Name:</span> <span class="value">{user_name or 'Not provided'}</span></div>
                        <div class="row"><span class="label">Email:</span> <span class="value">{user_email}</span></div>
                        <div class="row"><span class="label">Reason:</span> <span class="value">{reason_display}</span></div>
                        <div class="row"><span class="label">Request ID:</span> <span class="value">{request_id}</span></div>
                        <div class="row"><span class="label">Requested:</span> <span class="value">{requested_at}</span></div>
                    </div>
                    <p>Please review this request in the Jarvad admin dashboard under <strong>Deletion Requests</strong> and approve or reject it.</p>
                </div>
                <div class="footer">
                    <p>© {datetime.now().year} JardX Technologies. All rights reserved.</p>
                    <p>This is an automated notification. Please do not reply to this email.</p>
                </div>
            </div>
        </body>
    </html>
    """

    try:
        resend.Emails.send({
            "from": sender,
            "to": recipients,
            "subject": f"Account Deletion Request - {user_name or user_email}",
            "html": html_content
        })
        print(f"SUCCESS: Account deletion request email sent to {recipients}")
    except Exception as e:
        print("ERROR sending account deletion request email:", e)


def send_manual_payment_admin_email(admin_emails, user_name, user_email, amount, tx_ref, proof_url="", dashboard_url=""):
    """Notify admins that a manual bank-transfer payment is awaiting approval."""
    sender = os.getenv("RESEND_SENDER", "onboarding@resend.dev")
    recipients = [e.strip() for e in (admin_emails or []) if e and e.strip()]
    if not recipients:
        return

    amount_str = f"\u20a6{float(amount or 0):,.2f}"
    submitted_at = datetime.now().strftime("%d %b %Y, %I:%M %p")
    proof_block = f'<div class="row"><span class="label">Proof:</span> <span class="value"><a href="{proof_url}">View payment proof</a></span></div>' if proof_url else ""
    cta_block = f'<p style="text-align:center;margin:26px 0;"><a href="{dashboard_url}" style="background:#FF6900;color:#fff;text-decoration:none;padding:14px 28px;border-radius:10px;font-weight:bold;display:inline-block;">Review &amp; Approve Payment</a></p>' if dashboard_url else ""

    html_content = f"""
    <html>
        <head>
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f4f4; color: #333; margin: 0; padding: 20px; }}
                .container {{ max-width: 600px; margin: 0 auto; background-color: #ffffff; padding: 36px; border-radius: 12px; border-top: 6px solid #FF6900; box-shadow: 0 4px 15px rgba(0,0,0,0.06); }}
                h1 {{ color: #FF6900; margin: 0 0 8px 0; font-size: 22px; }}
                .badge {{ display: inline-block; padding: 6px 14px; background-color: #FFF1E6; color: #C2410C; border-radius: 20px; font-weight: bold; font-size: 12px; letter-spacing: .5px; text-transform: uppercase; }}
                .content {{ line-height: 1.6; font-size: 15px; color: #555; margin-top: 20px; }}
                .details {{ background-color: #FFF7ED; border-left: 4px solid #FF6900; padding: 18px 20px; border-radius: 8px; margin: 22px 0; }}
                .row {{ padding: 5px 0; }}
                .label {{ color: #777; font-weight: bold; }}
                .value {{ color: #222; }}
                .amount {{ font-size: 26px; font-weight: bold; color: #FF6900; margin: 8px 0 0 0; }}
                .footer {{ text-align: center; margin-top: 32px; font-size: 12px; color: #aaa; border-top: 1px solid #eee; padding-top: 18px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>New Bank Transfer Payment</h1>
                <span class="badge">Action Required</span>
                <p class="amount">{amount_str}</p>
                <div class="content">
                    <p>A user has submitted a manual bank-transfer payment that needs verification.</p>
                    <div class="details">
                        <div class="row"><span class="label">User:</span> <span class="value">{user_name or 'Not provided'}</span></div>
                        <div class="row"><span class="label">Email:</span> <span class="value">{user_email or 'N/A'}</span></div>
                        <div class="row"><span class="label">Reference:</span> <span class="value">{tx_ref}</span></div>
                        <div class="row"><span class="label">Submitted:</span> <span class="value">{submitted_at}</span></div>
                        {proof_block}
                    </div>
                    <p>Approve it in the admin dashboard under <strong>Finance &rarr; Pending</strong> to credit the user's wallet automatically.</p>
                    {cta_block}
                </div>
                <div class="footer">
                    <p>&copy; {datetime.now().year} JardX Technologies. All rights reserved.</p>
                    <p>This is an automated notification. Please do not reply to this email.</p>
                </div>
            </div>
        </body>
    </html>
    """

    try:
        resend.Emails.send({
            "from": sender,
            "to": recipients,
            "subject": f"New Bank Transfer Payment {amount_str} - {user_name or user_email}",
            "html": html_content
        })
        print(f"SUCCESS: Manual payment admin email sent to {recipients}")
    except Exception as e:
        print("ERROR sending manual payment admin email:", e)


def _is_partner(user):
    """Check if a user is a JardPartner (not a vendor)."""
    if not user:
        return False
    return user.get("partner_type") == "partner" or user.get("is_partner") is True


def process_referral_logic(user_id, amount, user_collection, transactions_collection, give_referrer_bonus=True):
    """
    Handles referral activation for the current user and bonus distribution for the referrer.
    
    Partners do NOT participate in the standard referral system.
    They earn only through process_partner_commission on property transactions.
    
    Tiers (standard users only):
      - below #5k:  non-qualifying (no activation, no bonus, no referrer payout)
      - #5k – #49k: 2% referral link (no self-bonus)
      - #50k – #99k: 5% referral link (no self-bonus)
      - #100k+:     5% referral link + 10% instant self-bonus on first deposit
    """
    # Ensure ID is a string for Astra DB lookup consistency
    uid_str = str(user_id)
    user = user_collection.find_one({"_id": uid_str})
    if not user:
        return
    
    # Partners get nothing from the standard referral system
    if _is_partner(user):
        return

    # Below the minimum qualifying amount: do not activate, do not pay the
    # referrer, and do NOT consume the one-time referral bonus.
    try:
        if float(amount) < 5000:
            return
    except (TypeError, ValueError):
        return
    
    # 1. Handle Activation (One-time based on first qualifying payment)
    if not user.get("is_referral_active", False):
        percentage = 0.0
        if amount >= 50000:
            percentage = 5.0
        elif amount >= 5000:
            percentage = 2.0
        
        if percentage > 0:
            updates = {"is_referral_active": True, "referral_percentage": percentage}
            user_collection.update_one(
                {"_id": user_id}, 
                {"$set": updates}
            )
            print(f"DEBUG: Referral activated for {user_id} with {percentage}%")
            
            # Self-bonus: 10% instant credit for #100k+ first deposit
            if amount >= 100000:
                self_bonus = amount * 0.10
                new_balance = float(user.get("wallet_balance", 0.0)) + self_bonus
                user_collection.update_one(
                    {"_id": user_id},
                    {"$set": {"wallet_balance": new_balance}}
                )
                transactions_collection.insert_one({
                    "tx_ref": f"REF-SELF-{secrets.token_hex(4).upper()}",
                    "user_id": uid_str,
                    "amount": self_bonus,
                    "gateway": "Referral System",
                    "type": "CREDIT",
                    "purpose": f"Referral Self-Bonus: First deposit #${amount:,.0f}",
                    "status": "SUCCESS",
                    "created_at": datetime.utcnow().isoformat()
                })
                print(f"DEBUG: Self-bonus of {self_bonus} (10%) credited to {user_id}")

    # 2. Handle Bonus for the Referrer (Only on the FIRST deposit)
    referrer_id = user.get("referred_by")
    bonus_already_paid = user.get("referral_bonus_paid", False)
    
    if give_referrer_bonus and referrer_id and not bonus_already_paid:
        # Resolve referrer
        referrer = user_collection.find_one({"_id": referrer_id})
        if referrer and referrer.get("is_referral_active"):
            # Partners don't earn through the standard referral system
            if _is_partner(referrer):
                # Mark bonus as paid to avoid repeated DB lookups on future deposits
                user_collection.update_one(
                    {"_id": uid_str},
                    {"$set": {"referral_bonus_paid": True}}
                )
            else:
                ref_percentage = referrer.get("referral_percentage", 0.0)
                if ref_percentage > 0:
                    bonus_amount = (ref_percentage / 100) * amount
                    
                    # Credit referrer's wallet
                    new_ref_balance = float(referrer.get("wallet_balance", 0.0)) + bonus_amount
                    user_collection.update_one(
                        {"_id": referrer_id}, 
                        {"$set": {"wallet_balance": new_ref_balance}}
                    )
                    
                    # Record transaction history for the bonus
                    transactions_collection.insert_one({
                        "tx_ref": f"REF-BONUS-{secrets.token_hex(4).upper()}",
                        "user_id": str(referrer_id),
                        "amount": bonus_amount,
                        "gateway": "Referral System",
                        "type": "CREDIT",
                        "purpose": f"Referral Bonus: {user.get('user_name', 'New User')} deposit",
                        "status": "SUCCESS",
                        "created_at": datetime.utcnow().isoformat()
                    })
                    print(f"DEBUG: Referral bonus of {bonus_amount} ({ref_percentage}%) credited to {referrer_id}")
                    
                    # 3. Mark the bonus as paid so it doesn't trigger on subsequent deposits
                    user_collection.update_one(
                        {"_id": uid_str}, 
                        {"$set": {"referral_bonus_paid": True}}
                    )


def process_partner_commission(user_id, amount, user_collection, transactions_collection):
    """
    Gives partner referrers commission on referred users' house payments.
    - Level 1 (direct): 10% to the direct referrer if they are a partner
    - Level 2 (indirect): 5% to the grand-referrer if they are a partner
    """
    try:
        amount = float(amount)
        if amount <= 0:
            return

        uid_str = str(user_id)
        user = user_collection.find_one({"_id": uid_str})
        if not user:
            return

        referrer_id = user.get("referred_by")
        if not referrer_id:
            return

        referrer = user_collection.find_one({"_id": referrer_id})
        if not referrer:
            return

        referrer_type = referrer.get("partner_type") or (
            "partner" if referrer.get("is_partner") else "normal"
        )

        if referrer_type == "partner":
            commission = amount * 0.10
            new_balance = float(referrer.get("wallet_balance", 0)) + commission
            user_collection.update_one(
                {"_id": referrer_id},
                {"$set": {"wallet_balance": new_balance}}
            )
            transactions_collection.insert_one({
                "tx_ref": f"PDC-{secrets.token_hex(4).upper()}",
                "user_id": str(referrer_id),
                "amount": commission,
                "gateway": "Referral System",
                "type": "CREDIT",
                "purpose": "Partner Direct Commission",
                "status": "SUCCESS",
                "created_at": datetime.utcnow().isoformat()
            })

            # Level 2: Indirect (grand-referrer)
            grand_referrer_id = referrer.get("referred_by")
            if grand_referrer_id:
                grand_referrer = user_collection.find_one({"_id": grand_referrer_id})
                if grand_referrer:
                    grand_type = grand_referrer.get("partner_type") or (
                        "partner" if grand_referrer.get("is_partner") else "normal"
                    )
                    if grand_type == "partner":
                        indirect = amount * 0.05
                        new_grand_balance = float(grand_referrer.get("wallet_balance", 0)) + indirect
                        user_collection.update_one(
                            {"_id": grand_referrer_id},
                            {"$set": {"wallet_balance": new_grand_balance}}
                        )
                        transactions_collection.insert_one({
                            "tx_ref": f"PIC-{secrets.token_hex(4).upper()}",
                            "user_id": str(grand_referrer_id),
                            "amount": indirect,
                            "gateway": "Referral System",
                            "type": "CREDIT",
                            "purpose": "Partner Indirect Commission",
                            "status": "SUCCESS",
                            "created_at": datetime.utcnow().isoformat()
                        })
    except Exception as e:
        logger.error(f"Partner commission error: {e}")


def send_jardproc_invoice_email(receiver_email, user_name, order_id, total_amount, items, address):
    sender = os.getenv("RESEND_SENDER", "onboarding@resend.dev")

    items_html = ""
    for item in items:
        items_html += f"""
        <tr>
            <td style="padding: 10px; border-bottom: 1px solid #eee;">{item.get('name')} (x{item.get('quantity')})</td>
            <td style="padding: 10px; border-bottom: 1px solid #eee; text-align: right;">₦{item.get('price') * item.get('quantity'):,.2f}</td>
        </tr>
        """

    html_content = f"""
    <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; background-color: #f4f4f4; color: #333; padding: 20px; }}
                .container {{ background-color: #ffffff; padding: 30px; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); max-width: 600px; margin: 0 auto; }}
                h1 {{ color: #ff6900; text-align: center; }}
                .details {{ text-align: left; background: #F6EEE9; padding: 20px; border-radius: 10px; margin: 20px 0; font-size: 14px; border-left: 4px solid #ff6900; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 15px; }}
                th {{ background-color: #ff6900; color: white; padding: 10px; text-align: left; }}
                .footer {{ margin-top: 30px; font-size: 12px; color: #888; text-align: center; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>JardProc Invoice</h1>
                <p>Hello <strong>{user_name}</strong>,</p>
                <p>Thank you for shopping on JardProc Store! We have successfully processed your order and payment.</p>

                <div class="details">
                    <p><strong>Order ID:</strong> #{order_id}</p>
                    <p><strong>Delivery Address:</strong> {address}</p>
                </div>

                <table>
                    <thead>
                        <tr>
                            <th>Item</th>
                            <th style="text-align: right;">Price</th>
                        </tr>
                    </thead>
                    <tbody>
                        {items_html}
                        <tr>
                            <td style="padding: 10px; font-weight: bold;">Grand Total</td>
                            <td style="padding: 10px; text-align: right; font-weight: bold; color: #ff6900;">₦{total_amount:,.2f}</td>
                        </tr>
                    </tbody>
                </table>

                <p style="margin-top: 20px; text-align: center;">You can track this order in real-time inside your mobile application.</p>
                <div class="footer">© {datetime.now().year} JardProc Store. All rights reserved.</div>
            </div>
        </body>
    </html>
    """

    try:
        resend.Emails.send({
            "from": sender,
            "to": [receiver_email],
            "subject": f"Invoice: Order #{order_id} Successful",
            "html": html_content
        })
        print(f"SUCCESS: Invoice Email sent successfully to {receiver_email}")
    except Exception as e:
        print("ERROR sending invoice email:", e)


def send_distributor_welcome_email(receiver_email, user_name, distributor_id, product_name, variant_label, quantity, total_amount):
    sender = os.getenv("RESEND_SENDER", "onboarding@resend.dev")
    variant_text = f"<p><strong>Variant:</strong> {variant_label}</p>" if variant_label else ""

    html_content = f"""
    <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; background-color: #f4f4f4; color: #333; padding: 20px; }}
                .container {{ background-color: #ffffff; padding: 30px; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); max-width: 600px; margin: 0 auto; }}
                h1 {{ color: #ff6900; text-align: center; }}
                .details {{ text-align: left; background: #F6EEE9; padding: 20px; border-radius: 10px; margin: 20px 0; font-size: 14px; border-left: 4px solid #ff6900; }}
                .footer {{ margin-top: 30px; font-size: 12px; color: #888; text-align: center; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Welcome, Distributor</h1>
                <p>Hello <strong>{user_name}</strong>,</p>
                <p>Your JardX distributor registration and payment were successful.</p>

                <div class="details">
                    <p><strong>Distributor ID:</strong> {distributor_id}</p>
                    <p><strong>Product:</strong> {product_name}</p>
                    {variant_text}
                    <p><strong>Quantity:</strong> {quantity}</p>
                    <p><strong>Amount Paid:</strong> ₦{total_amount:,.2f}</p>
                </div>

                <p>We will send the next steps to your email and SMS within the next 24 hours.</p>
                <div class="footer">© {datetime.now().year} JardX. All rights reserved.</div>
            </div>
        </body>
    </html>
    """

    try:
        resend.Emails.send({
            "from": sender,
            "to": [receiver_email],
            "subject": "Welcome to JardX Distributors",
            "html": html_content
        })
        print(f"SUCCESS: Distributor email sent successfully to {receiver_email}")
    except Exception as e:
        print("ERROR sending distributor email:", e)


# ─────────────────────────────────────────────
# SUBSCRIPTION & REMINDER EMAIL FUNCTIONS
# ─────────────────────────────────────────────

def send_subscription_reminder_email(receiver_email, user_name, plan_type, fee, days=30):
    sender = os.getenv("RESEND_SENDER", "onboarding@resend.dev")
    urgency = "⚠️ " if days <= 7 else ""
    subject = f"{urgency}Reminder: Your {plan_type} Subscription Expires in {days} Days"

    html = f"""<html><head><style>
    body{{font-family:Arial,sans-serif;background:#f4f4f4;color:#333;padding:20px}}
    .container{{background:#fff;padding:30px;border-radius:10px;max-width:600px;margin:0 auto}}
    h1{{color:#ff6900}} .fee{{font-size:24px;font-weight:bold;color:#ff6900;margin:20px 0}}
    .footer{{margin-top:30px;font-size:12px;color:#888;text-align:center}}
    </style></head><body>
    <div class="container">
    <h1>{'⚠️ Action Required!' if days <= 7 else 'Subscription Reminder'}</h1>
    <p>Hello <strong>{user_name}</strong>,</p>
    <p>Your <strong>{plan_type}</strong> subscription will expire in <strong>{days} days</strong>.</p>
    <p>Renewal Fee: <span class="fee">₦{fee:,.2f}</span></p>
    {"<p><strong>Note:</strong> If not renewed by the expiry date, your subscription will expire and you may lose access to your {plan_type} dashboard.</p>" if days > 3 else '<p><strong>Urgent:</strong> Renew now to avoid service interruption.</p>'}
    <p>Log into your JardX app to renew or enable auto-renewal.</p>
    <div class="footer">© {datetime.now().year} JardX. All rights reserved.</div>
    </div></body></html>"""

    try:
        resend.Emails.send({"from": sender, "to": [receiver_email], "subject": subject, "html": html})
        print(f"SUCCESS: Subscription reminder email sent to {receiver_email}")
    except Exception as e:
        print(f"ERROR sending subscription reminder: {e}")


def send_subscription_penalty_email(receiver_email, user_name, plan_type, fee, total_due):
    sender = os.getenv("RESEND_SENDER", "onboarding@resend.dev")
    subject = f"⚠️ {plan_type} Subscription Expired - Penalty Applied"

    html = f"""<html><head><style>
    body{{font-family:Arial,sans-serif;background:#f4f4f4;color:#333;padding:20px}}
    .container{{background:#fff;padding:30px;border-radius:10px;max-width:600px;margin:0 auto}}
    h1{{color:#e74c3c}} .fee{{font-size:20px;font-weight:bold}} .total{{font-size:28px;font-weight:bold;color:#e74c3c;margin:20px 0}}
    .footer{{margin-top:30px;font-size:12px;color:#888;text-align:center}}
    </style></head><body>
    <div class="container">
    <h1>⚠️ Subscription Expired</h1>
    <p>Hello <strong>{user_name}</strong>,</p>
    <p>Your <strong>{plan_type}</strong> subscription has expired. A 5% late penalty has been applied.</p>
    <p>Fee: <span class="fee">₦{fee:,.2f}</span></p>
    <p>Total Due (incl. penalty): <span class="total">₦{total_due:,.2f}</span></p>
    <p>Renew now in the JardX app to restore your {plan_type} benefits.</p>
    <div class="footer">© {datetime.now().year} JardX. All rights reserved.</div>
    </div></body></html>"""

    try:
        resend.Emails.send({"from": sender, "to": [receiver_email], "subject": subject, "html": html})
        print(f"SUCCESS: Penalty email sent to {receiver_email}")
    except Exception as e:
        print(f"ERROR sending penalty email: {e}")


def send_installment_reminder_email(receiver_email, user_name, property_name, monthly, penalty=0, days=7):
    sender = os.getenv("RESEND_SENDER", "onboarding@resend.dev")
    urgency = "⚠️ " if days <= 3 else ""
    subject = f"{urgency}Reminder: Installment Due in {days} Days - {property_name}"
    total = monthly + penalty
    penalty_line = f"<p>Late Penalty: ₦{penalty:,.2f}</p>" if penalty else ""

    html = f"""<html><head><style>
    body{{font-family:Arial,sans-serif;background:#f4f4f4;color:#333;padding:20px}}
    .container{{background:#fff;padding:30px;border-radius:10px;max-width:600px;margin:0 auto}}
    h1{{color:#ff6900}} .amount{{font-size:24px;font-weight:bold;color:#ff6900;margin:20px 0}}
    .footer{{margin-top:30px;font-size:12px;color:#888;text-align:center}}
    </style></head><body>
    <div class="container">
    <h1>{'⏰ Payment Reminder' if days <= 3 else 'Payment Reminder'}</h1>
    <p>Hello <strong>{user_name}</strong>,</p>
    <p>Your next installment for <strong>{property_name}</strong> is due in <strong>{days} days</strong>.</p>
    <p>Monthly Amount: <span class="amount">₦{monthly:,.2f}</span></p>
    {penalty_line}
    <p>Total Due: <span class="amount">₦{total:,.2f}</span></p>
    {"<p><strong>Note:</strong> Late payment incurs a 5% penalty on the monthly amount.</p>" if days > 3 else '<p><strong>Urgent:</strong> Pay now to avoid late penalties.</p>'}
    <p>Log into JardX to make your payment or enable auto-debit.</p>
    <div class="footer">© {datetime.now().year} JardX. All rights reserved.</div>
    </div></body></html>"""

    try:
        resend.Emails.send({"from": sender, "to": [receiver_email], "subject": subject, "html": html})
        print(f"SUCCESS: Installment reminder sent to {receiver_email}")
    except Exception as e:
        print(f"ERROR sending installment reminder: {e}")


def send_installment_penalty_email(receiver_email, user_name, property_name, monthly, penalty):
    sender = os.getenv("RESEND_SENDER", "onboarding@resend.dev")
    subject = f"⚠️ Late Penalty Applied - {property_name}"
    total = monthly + penalty

    html = f"""<html><head><style>
    body{{font-family:Arial,sans-serif;background:#f4f4f4;color:#333;padding:20px}}
    .container{{background:#fff;padding:30px;border-radius:10px;max-width:600px;margin:0 auto}}
    h1{{color:#e74c3c}} .total{{font-size:28px;font-weight:bold;color:#e74c3c;margin:20px 0}}
    .footer{{margin-top:30px;font-size:12px;color:#888;text-align:center}}
    </style></head><body>
    <div class="container">
    <h1>⚠️ Late Penalty Applied</h1>
    <p>Hello <strong>{user_name}</strong>,</p>
    <p>Your installment for <strong>{property_name}</strong> is overdue. A 5% penalty (₦{penalty:,.2f}) has been added.</p>
    <p>Monthly Due: ₦{monthly:,.2f}<br>Penalty: ₦{penalty:,.2f}</p>
    <p>Total Due Now: <span class="total">₦{total:,.2f}</span></p>
    <p>Pay now to avoid further penalties.</p>
    <div class="footer">© {datetime.now().year} JardX. All rights reserved.</div>
    </div></body></html>"""

    try:
        resend.Emails.send({"from": sender, "to": [receiver_email], "subject": subject, "html": html})
        print(f"SUCCESS: Installment penalty email sent to {receiver_email}")
    except Exception as e:
        print(f"ERROR sending installment penalty: {e}")


def send_installment_auto_debit_failed_email(receiver_email, user_name, property_name, total_due):
    sender = os.getenv("RESEND_SENDER", "onboarding@resend.dev")
    subject = f"⚠️ Auto-Debit Failed - {property_name}"

    html = f"""<html><head><style>
    body{{font-family:Arial,sans-serif;background:#f4f4f4;color:#333;padding:20px}}
    .container{{background:#fff;padding:30px;border-radius:10px;max-width:600px;margin:0 auto}}
    h1{{color:#e74c3c}} .amount{{font-size:24px;font-weight:bold;color:#e74c3c}}
    .footer{{margin-top:30px;font-size:12px;color:#888;text-align:center}}
    </style></head><body>
    <div class="container">
    <h1>⚠️ Auto-Debit Failed</h1>
    <p>Hello <strong>{user_name}</strong>,</p>
    <p>We attempted to auto-deduct your installment for <strong>{property_name}</strong> but your wallet balance was insufficient.</p>
    <p>Amount Due: <span class="amount">₦{total_due:,.2f}</span></p>
    <p>Auto-debit has been disabled. Please top up your wallet and pay manually, or re-enable auto-debit.</p>
    <div class="footer">© {datetime.now().year} JardX. All rights reserved.</div>
    </div></body></html>"""

    try:
        resend.Emails.send({"from": sender, "to": [receiver_email], "subject": subject, "html": html})
    except Exception as e:
        print(f"ERROR sending auto-debit failed: {e}")


def send_jardkidz_reminder_email(receiver_email, user_name, child_name, monthly, penalty=0, days=7):
    sender = os.getenv("RESEND_SENDER", "onboarding@resend.dev")
    urgency = "⚠️ " if days <= 3 else ""
    subject = f"{urgency}Reminder: JardKidz Contribution Due in {days} Days"
    total = monthly + penalty
    penalty_line = f"<p>Late Penalty: ₦{penalty:,.2f}</p>" if penalty else ""

    html = f"""<html><head><style>
    body{{font-family:Arial,sans-serif;background:#f4f4f4;color:#333;padding:20px}}
    .container{{background:#fff;padding:30px;border-radius:10px;max-width:600px;margin:0 auto}}
    h1{{color:#ff6900}} .amount{{font-size:24px;font-weight:bold;color:#ff6900;margin:20px 0}}
    .footer{{margin-top:30px;font-size:12px;color:#888;text-align:center}}
    </style></head><body>
    <div class="container">
    <h1>JardKidz Contribution Reminder</h1>
    <p>Hello <strong>{user_name}</strong>,</p>
    <p>Your JardKidz contribution for <strong>{child_name}</strong> is due in <strong>{days} days</strong>.</p>
    <p>Monthly Amount: <span class="amount">₦{monthly:,.2f}</span></p>
    {penalty_line}
    <p>Total Due: <span class="amount">₦{total:,.2f}</span></p>
    {"<p>Log into JardX to make your contribution or enable auto-debit.</p>" if days > 3 else '<p><strong>Urgent:</strong> Pay now to avoid late penalties.</p>'}
    <div class="footer">© {datetime.now().year} JardX. All rights reserved.</div>
    </div></body></html>"""

    try:
        resend.Emails.send({"from": sender, "to": [receiver_email], "subject": subject, "html": html})
    except Exception as e:
        print(f"ERROR sending jardkidz reminder: {e}")


def send_jardkidz_penalty_email(receiver_email, user_name, child_name, monthly, penalty):
    sender = os.getenv("RESEND_SENDER", "onboarding@resend.dev")
    subject = f"⚠️ Late Penalty Applied - JardKidz ({child_name})"
    total = monthly + penalty

    html = f"""<html><head><style>
    body{{font-family:Arial,sans-serif;background:#f4f4f4;color:#333;padding:20px}}
    .container{{background:#fff;padding:30px;border-radius:10px;max-width:600px;margin:0 auto}}
    h1{{color:#e74c3c}} .total{{font-size:28px;font-weight:bold;color:#e74c3c;margin:20px 0}}
    .footer{{margin-top:30px;font-size:12px;color:#888;text-align:center}}
    </style></head><body>
    <div class="container">
    <h1>⚠️ Late Penalty Applied</h1>
    <p>Hello <strong>{user_name}</strong>,</p>
    <p>Your JardKidz contribution for <strong>{child_name}</strong> is overdue. A 5% penalty (₦{penalty:,.2f}) has been added.</p>
    <p>Monthly Due: ₦{monthly:,.2f}<br>Penalty: ₦{penalty:,.2f}</p>
    <p>Total Due Now: <span class="total">₦{total:,.2f}</span></p>
    <p>Top up now to avoid further penalties.</p>
    <div class="footer">© {datetime.now().year} JardX. All rights reserved.</div>
    </div></body></html>"""

    try:
        resend.Emails.send({"from": sender, "to": [receiver_email], "subject": subject, "html": html})
    except Exception as e:
        print(f"ERROR sending jardkidz penalty: {e}")


def send_subscription_auto_debit_failed_email(receiver_email, user_name, plan_type, fee):
    sender = os.getenv("RESEND_SENDER", "onboarding@resend.dev")
    subject = f"⚠️ Auto-Renewal Failed - {plan_type} Subscription"

    html = f"""<html><head><style>
    body{{font-family:Arial,sans-serif;background:#f4f4f4;color:#333;padding:20px}}
    .container{{background:#fff;padding:30px;border-radius:10px;max-width:600px;margin:0 auto}}
    h1{{color:#e74c3c}} .amount{{font-size:24px;font-weight:bold;color:#e74c3c}}
    .footer{{margin-top:30px;font-size:12px;color:#888;text-align:center}}
    </style></head><body>
    <div class="container">
    <h1>⚠️ Auto-Renewal Failed</h1>
    <p>Hello <strong>{user_name}</strong>,</p>
    <p>We attempted to auto-renew your <strong>{plan_type}</strong> subscription but your wallet balance was insufficient.</p>
    <p>Amount Due: <span class="amount">₦{fee:,.2f}</span></p>
    <p>Auto-renewal has been disabled. Please top up your wallet and renew manually, or re-enable auto-renewal.</p>
    <div class="footer">© {datetime.now().year} JardX. All rights reserved.</div>
    </div></body></html>"""

    try:
        resend.Emails.send({"from": sender, "to": [receiver_email], "subject": subject, "html": html})
    except Exception as e:
        print(f"ERROR sending sub auto-debit failed: {e}")


def send_push_notification_to_user(user_doc: dict, title: str, body: str, data_payload: dict = None):
    """
    Sends an Expo push notification to a user if they have enabled notifications
    and have a valid push token.
    Supports rich notifications with image attachments by passing an 'image' key inside data_payload.
    """
    try:
        import requests
        import json
        
        # Check push token and if push notification setting is enabled
        push_token = user_doc.get("push_token")
        settings = user_doc.get("notification_settings", {})
        
        # Guard push-send logic: skip if no token or if the push setting is false
        if not push_token:
            return False
            
        if not settings.get("push", False):
            # User has not toggled push notifications on
            return False
            
        payload = {
            "to": push_token,
            "title": title,
            "body": body,
            "data": data_payload or {}
        }
        
        # If an image URL is in the data payload, support rich previews (iOS & Android)
        if data_payload and "image" in data_payload and data_payload["image"]:
            image_url = data_payload["image"]
            payload["mutableContent"] = True
            # Support richContent.image for Android/some platforms
            payload["richContent"] = {"image": image_url}
            
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        
        url = "https://exp.host/--/api/v2/push/send"
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        
        # Return success status
        return response.status_code == 200
    except Exception as e:
        print("ERROR sending push notification:", e)
        return False

