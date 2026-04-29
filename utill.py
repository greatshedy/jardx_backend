from argon2 import PasswordHasher
import random
from dotenv import load_dotenv
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
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


# Use absolute path to ensure .env is loaded regardless of current working directory
current_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(current_dir, ".env"))

ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "fallback_secret_keep_this_secure")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 60))
BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://10.12.228.168:8000")

def get_image_url(image_path: str) -> str:
    """
    Converts a relative image path (/uploads/...) to a full URL.
    If the input is already a URL or base64, returns it as is.
    """
    if not image_path:
        return ""
    if image_path.startswith("http") or image_path.startswith("data:"):
        return image_path
    
    return f"{BACKEND_BASE_URL.rstrip('/')}/{image_path.lstrip('/')}"


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




def get_smtp_connection():
    """
    Attempts to establish an SMTP connection with fallback:
    1. Try Port 465 (SSL)
    2. Try Port 587 (TLS)
    """
    sender_email = os.getenv("SENDER_EMAIL")
    sender_password = os.getenv("EMAIL_PASSWORD")
    smtp_server = os.getenv("SMTP_SERVER")

    # Try Port 465 (SSL) first
    try:
        print(f"DEBUG: Attempting SMTP SSL on {smtp_server}:465...")
        server = smtplib.SMTP_SSL(smtp_server, 465, timeout=15)
        server.login(sender_email, sender_password)
        print("DEBUG: SMTP SSL Connected Successfully")
        return server
    except Exception as e:
        print(f"DEBUG: SSL connection failed or timed out: {e}")
        print("DEBUG: Falling back to TLS on Port 587...")

    # Fallback to Port 587 (TLS)
    try:
        server = smtplib.SMTP(smtp_server, 587, timeout=15)
        server.starttls()
        server.login(sender_email, sender_password)
        print("DEBUG: SMTP TLS Connected Successfully")
        return server
    except Exception as e:
        print(f"DEBUG: TLS connection also failed: {e}")
        raise e

def send_email(receiver_email, otp):
    # Create the email container
    message = MIMEMultipart("alternative")
    message["Subject"] = "Your OTP Code for JardX"
    message["From"] = os.getenv("SENDER_EMAIL")
    message["To"] = receiver_email

    # Plain text fallback
    plain_text = f"Your OTP code is {otp}. It expires in 5 minutes."
    message.attach(MIMEText(plain_text, "plain"))

    # HTML email with inline CSS
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
    message.attach(MIMEText(html_content, "html"))

    try:
        server = get_smtp_connection()
        server.send_message(message)
        server.quit()
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

from email.mime.image import MIMEImage

def send_purchase_email(receiver_email, user_name, house_name, plan_type, amount_paid, remaining_balance, image_data):
    sender_email = os.getenv("SENDER_EMAIL")
    sender_password = os.getenv("EMAIL_PASSWORD")
    
    if not sender_email or not sender_password:
        print("Email configuration missing, cannot send purchase email.")
        return

    # Use 'related' to embed image
    message = MIMEMultipart("related")
    message["Subject"] = f"Confirmation: {house_name} ({plan_type.title()})"
    message["From"] = sender_email
    message["To"] = receiver_email

    body = MIMEMultipart("alternative")
    message.attach(body)

    is_url = image_data.startswith("http")
    
    # HTML email with inline CSS
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
        img_src = "cid:house_image" if not is_url else image_data
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
    
    body.attach(MIMEText(html_content, "html"))

    if image_data and not is_url:
        try:
            if image_data.startswith("data:image"):
                base64_data = image_data.split(",", 1)[1]
                image_bytes = base64.b64decode(base64_data)
            else:
                try:
                    image_bytes = base64.b85decode(image_data)
                except Exception:
                    image_bytes = base64.b64decode(image_data)
            
            msg_image = MIMEImage(image_bytes)
            msg_image.add_header('Content-ID', '<house_image>')
            msg_image.add_header('Content-Disposition', 'inline')
            message.attach(msg_image)

        except Exception as e:
            print("Could not attach image to email:", e)

    try:
        server = get_smtp_connection()
        server.send_message(message)
        server.quit()
        print(f"SUCCESS: Purchase Email sent successfully to {receiver_email}")
    except Exception as e:
        print("ERROR sending purchase email:", e)

def send_jard_kidz_email(receiver_email, user_name, plan_details, is_setup=True):
    sender_email = os.getenv("SENDER_EMAIL")
    sender_password = os.getenv("EMAIL_PASSWORD")
    
    if not sender_email or not sender_password:
        return

    child_name = plan_details.get("child_name", "your child")
    plan_type_raw = plan_details.get("plan_type", "child_savings")
    plan_type = "Property Plan" if plan_type_raw == "child_property" else "Savings Plan"
    amount = float(plan_details.get("amount_paid", 0))
    months_paid = plan_details.get("months_paid", 1)
    total_months = plan_details.get("total_months", 0)
    
    subject = f"Confirmation: JardKidz {plan_type} Setup" if is_setup else f"Receipt: JardKidz {plan_type} Top-up"

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = sender_email
    message["To"] = receiver_email

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
                <!-- Deep Link via Web Bridge: Gmail blocks custom schemes; the bridge page silently redirects to jardx:// -->
                <a href="{os.getenv('MOBILE_URL', '#')}?autoOpen=true&type=kidz&name={child_name}" class="cta">Open in JardX App</a>
                <p style="text-align: center; margin-top: 10px;">
                    <a href="{os.getenv('MOBILE_URL', '#')}?type=kidz&name={child_name}" style="color: #ff6900; font-size: 14px; text-decoration: none;">View in Browser</a>
                </p>
                <div class="footer">
                    <p>© {datetime.now().year} JardX Technologies. All rights reserved.</p>
                    <p>This is an automated receipt. Please do not reply to this email.</p>
                </div>
            </div>
        </body>
    </html>
    """
    message.attach(MIMEText(html_content, "html"))

    try:
        server = get_smtp_connection()
        server.send_message(message)
        server.quit()
        print(f"SUCCESS: JardKidz Email sent to {receiver_email}")
    except Exception as e:
        print("ERROR sending JardKidz email:", e)

def send_wallet_credit_email(receiver_email, user_name, amount, new_balance):
    sender_email = os.getenv("SENDER_EMAIL")
    sender_password = os.getenv("EMAIL_PASSWORD")
    
    if not sender_email or not sender_password:
        return

    message = MIMEMultipart("alternative")
    message["Subject"] = "Wallet Credit Confirmation - JardX"
    message["From"] = sender_email
    message["To"] = receiver_email

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
                <!-- Deep Link via Web Bridge: Gmail blocks custom schemes; the bridge page silently redirects to jardx:// -->
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
    message.attach(MIMEText(html_content, "html"))

    try:
        server = get_smtp_connection()
        server.send_message(message)
        server.quit()
        print(f"SUCCESS: Wallet Credit Email sent to {receiver_email}")
    except Exception as e:
        print("ERROR sending wallet credit email:", e)


def process_referral_logic(user_id, amount, user_collection, transactions_collection):
    """
    Handles referral activation for the current user and bonus distribution for the referrer.
    """
    # Ensure ID is a string for Astra DB lookup consistency
    uid_str = str(user_id)
    user = user_collection.find_one({"_id": uid_str})
    if not user:
        return
    
    # 1. Handle Activation (One-time based on first qualifying payment)
    if not user.get("is_referral_active", False):
        percentage = 0.0
        if amount >= 100000:
            percentage = 10.0
        elif amount >= 50000:
            percentage = 5.0
        elif amount >= 5000:
            percentage = 2.0
        
        if percentage > 0:
            user_collection.update_one(
                {"_id": user_id}, 
                {"$set": {"is_referral_active": True, "referral_percentage": percentage}}
            )
            print(f"DEBUG: Referral activated for {user_id} with {percentage}%")

    # 2. Handle Bonus for the Referrer (Only on the FIRST deposit)
    referrer_id = user.get("referred_by")
    bonus_already_paid = user.get("referral_bonus_paid", False)
    
    if referrer_id and not bonus_already_paid:
        # Resolve referrer
        referrer = user_collection.find_one({"_id": referrer_id})
        if referrer and referrer.get("is_referral_active"):
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
