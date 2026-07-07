import os
import secrets
import logging
import datetime
from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from db.database import (
    user_collection,
    withdraw_requests_collection,
)
from utill import get_token, VerifyHashed, generate_otp
from utils.email_service import send_withdraw_otp_email

logger = logging.getLogger("jardx")

router = APIRouter(prefix="/users", tags=["Withdraw"])

WITHDRAW_MIN_AMOUNT = 5000.0
OTP_RESEND_COOLDOWN_SECONDS = 60
OTP_EXPIRY_MINUTES = 10
OTP_MAX_ATTEMPTS = 5


def _mask_email(email: str) -> str:
    if not email or "@" not in email:
        return "***"
    name, domain = email.split("@", 1)
    if len(name) <= 2:
        return f"{name[0]}***@{domain}"
    return f"{name[:2]}***{name[-1:]}@{domain}"


def _bank_summary(bank: dict) -> str:
    if not bank:
        return "your bank account"
    name = bank.get("bank_name", "Bank")
    last4 = (bank.get("account_number") or "")[-4:]
    return f"{name} (...{last4})" if last4 else name


class WithdrawRequestPayload(BaseModel):
    amount: float
    bank_account_id: str
    pin: str | int


class WithdrawVerifyOtpPayload(BaseModel):
    withdraw_id: str
    otp: str | int


class WithdrawResendOtpPayload(BaseModel):
    withdraw_id: str


@router.get("/earnings")
async def get_earnings(data: dict = Depends(get_token)):
    try:
        user_id = data.get("id")
        user = user_collection.find_one({"_id": user_id})
        if not user:
            return JSONResponse({"message": "User not found", "status": 404})

        earnings_balance = user.get("earnings_balance")
        if earnings_balance is None:
            earnings_balance = float(user.get("wallet_balance", 0) or 0)

        total_withdrawn = 0.0
        try:
            cursor = withdraw_requests_collection.find({
                "user_id": user_id,
                "state": {"$in": ["pending_admin", "paid"]},
            })
            for doc in cursor:
                total_withdrawn += float(doc.get("amount", 0) or 0)
        except Exception as e:
            logger.error(f"Error summing withdraws: {e}")

        pending_amount = 0.0
        try:
            cursor = withdraw_requests_collection.find({
                "user_id": user_id,
                "state": {"$in": ["pending_otp"]},
            })
            for doc in cursor:
                pending_amount += float(doc.get("amount", 0) or 0)
        except Exception as e:
            logger.error(f"Error summing pending withdraws: {e}")

        available = max(0.0, float(earnings_balance) - pending_amount)

        return {
            "status": 200,
            "data": {
                "available_balance": round(available, 2),
                "total_earned": round(float(earnings_balance), 2),
                "total_withdrawn": round(total_withdrawn, 2),
                "pending_amount": round(pending_amount, 2),
                "minimum_amount": WITHDRAW_MIN_AMOUNT,
            },
        }
    except Exception as e:
        logger.error(f"Error in /earnings: {e}")
        return JSONResponse({"message": str(e), "status": 500})


@router.post("/withdraw/request")
async def withdraw_request(
    payload: WithdrawRequestPayload,
    background_tasks: BackgroundTasks,
    data: dict = Depends(get_token),
):
    try:
        user_id = data.get("id")
        user = user_collection.find_one({"_id": user_id})
        if not user:
            return JSONResponse({"message": "User not found", "status": 404})

        amount = float(payload.amount or 0)
        bank_id = payload.bank_account_id
        pin = str(payload.pin)

        if amount < WITHDRAW_MIN_AMOUNT:
            return JSONResponse(
                {
                    "message": f"Minimum withdraw amount is \u20a6{WITHDRAW_MIN_AMOUNT:,.0f}.",
                    "status": 400,
                }
            )

        if not bank_id:
            return JSONResponse(
                {"message": "Please select a bank account.", "status": 400}
            )

        bank_accounts = user.get("bank_accounts", []) or []
        selected_bank = next((b for b in bank_accounts if str(b.get("id")) == str(bank_id)), None)
        if not selected_bank:
            return JSONResponse(
                {"message": "Bank account not found. Please re-select.", "status": 404}
            )

        stored_pin = user.get("transaction_pin")
        if not stored_pin:
            return JSONResponse(
                {"message": "No transaction PIN set. Please set one first.", "status": 400}
            )
        try:
            is_valid = VerifyHashed(stored_pin, pin)
        except Exception:
            is_valid = False
        if not is_valid:
            return JSONResponse(
                {"message": "Incorrect transaction PIN.", "status": 401}
            )

        earnings_balance = user.get("earnings_balance")
        if earnings_balance is None:
            earnings_balance = float(user.get("wallet_balance", 0) or 0)
        if float(earnings_balance) < amount:
            return JSONResponse(
                {
                    "message": f"Insufficient earnings balance. Available: \u20a6{float(earnings_balance):,.2f}.",
                    "status": 400,
                }
            )

        otp = generate_otp(length=6)
        otp_expiry = datetime.datetime.utcnow() + datetime.timedelta(minutes=OTP_EXPIRY_MINUTES)
        now_iso = datetime.datetime.utcnow().isoformat()

        withdraw_doc = {
            "id": secrets.token_hex(8),
            "user_id": user_id,
            "user_email": user.get("email"),
            "user_name": user.get("user_name", "User"),
            "amount": round(amount, 2),
            "bank_account": {
                "id": selected_bank.get("id"),
                "bank_name": selected_bank.get("bank_name"),
                "account_number": selected_bank.get("account_number"),
                "account_name": selected_bank.get("account_name"),
                "account_type": selected_bank.get("account_type"),
            },
            "state": "pending_otp",
            "otp_hash": None,
            "otp_plain": otp,
            "otp_attempts": 0,
            "otp_expires_at": otp_expiry.isoformat(),
            "otp_last_sent_at": now_iso,
            "created_at": now_iso,
            "updated_at": now_iso,
        }
        try:
            withdraw_requests_collection.insert_one(withdraw_doc)
        except Exception as e:
            logger.error(f"Failed to insert withdraw doc: {e}")
            return JSONResponse(
                {"message": "Could not create withdraw request. Try again.", "status": 500}
            )

        bank_summary = _bank_summary(selected_bank)
        background_tasks.add_task(
            send_withdraw_otp_email,
            user.get("email"),
            user.get("user_name", "there"),
            otp,
            amount,
            bank_summary,
        )

        return JSONResponse(
            {
                "status": 200,
                "message": "OTP sent to your email.",
                "withdraw_id": withdraw_doc["id"],
                "otp_sent_to": _mask_email(user.get("email")),
                "amount": withdraw_doc["amount"],
                "bank_summary": bank_summary,
            }
        )
    except Exception as e:
        logger.error(f"Error in /withdraw/request: {e}")
        return JSONResponse({"message": str(e), "status": 500})


@router.post("/withdraw/verify-otp")
async def withdraw_verify_otp(
    payload: WithdrawVerifyOtpPayload,
    data: dict = Depends(get_token),
):
    try:
        user_id = data.get("id")
        withdraw_id = payload.withdraw_id
        otp = str(payload.otp).strip()

        record = withdraw_requests_collection.find_one({"id": withdraw_id, "user_id": user_id})
        if not record:
            return JSONResponse({"message": "Withdraw request not found.", "status": 404})

        state = record.get("state")
        if state == "pending_admin":
            return JSONResponse(
                {
                    "status": 200,
                    "state": "pending_admin",
                    "message": "Already verified. Awaiting admin processing.",
                }
            )
        if state in ("paid", "rejected"):
            return JSONResponse(
                {
                    "message": f"This withdraw request is already {state}.",
                    "status": 400,
                }
            )

        if record.get("otp_attempts", 0) >= OTP_MAX_ATTEMPTS:
            return JSONResponse(
                {
                    "message": "Too many wrong attempts. Please resend the code and try again in 10 minutes.",
                    "status": 429,
                }
            )

        try:
            expires_at = datetime.datetime.fromisoformat(record["otp_expires_at"])
        except Exception:
            expires_at = None
        if expires_at and datetime.datetime.utcnow() > expires_at:
            return JSONResponse(
                {"message": "Code expired. Please resend a new code.", "status": 410}
            )

        if str(record.get("otp_plain")) != otp:
            withdraw_requests_collection.update_one(
                {"id": withdraw_id},
                {"$inc": {"otp_attempts": 1}, "$set": {"updated_at": datetime.datetime.utcnow().isoformat()}},
            )
            return JSONResponse(
                {"message": "Invalid code. Please try again.", "status": 401}
            )

        user = user_collection.find_one({"_id": user_id})
        if not user:
            return JSONResponse({"message": "User not found", "status": 404})

        earnings_balance = user.get("earnings_balance")
        if earnings_balance is None:
            earnings_balance = float(user.get("wallet_balance", 0) or 0)
        amount = float(record.get("amount", 0))
        if float(earnings_balance) < amount:
            return JSONResponse(
                {
                    "message": "Insufficient earnings balance. Withdraw request cancelled.",
                    "status": 400,
                }
            )

        new_earnings = round(float(earnings_balance) - amount, 2)
        new_wallet = round(float(user.get("wallet_balance", 0) or 0) - amount, 2)
        user_collection.update_one(
            {"_id": user_id},
            {
                "$set": {
                    "earnings_balance": new_earnings,
                    "wallet_balance": new_wallet,
                    "updated_at": datetime.datetime.utcnow().isoformat(),
                }
            },
        )

        now_iso = datetime.datetime.utcnow().isoformat()
        withdraw_requests_collection.update_one(
            {"id": withdraw_id},
            {
                "$set": {
                    "state": "pending_admin",
                    "verified_at": now_iso,
                    "updated_at": now_iso,
                    "otp_plain": None,
                }
            },
        )

        return JSONResponse(
            {
                "status": 200,
                "state": "pending_admin",
                "message": "Withdraw verified. Admin will process within 24 hours.",
                "withdraw_id": withdraw_id,
                "earnings_balance": new_earnings,
            }
        )
    except Exception as e:
        logger.error(f"Error in /withdraw/verify-otp: {e}")
        return JSONResponse({"message": str(e), "status": 500})


@router.post("/withdraw/resend-otp")
async def withdraw_resend_otp(
    payload: WithdrawResendOtpPayload,
    background_tasks: BackgroundTasks,
    data: dict = Depends(get_token),
):
    try:
        user_id = data.get("id")
        withdraw_id = payload.withdraw_id

        record = withdraw_requests_collection.find_one({"id": withdraw_id, "user_id": user_id})
        if not record:
            return JSONResponse({"message": "Withdraw request not found.", "status": 404})

        state = record.get("state")
        if state in ("paid", "rejected", "pending_admin"):
            return JSONResponse(
                {"message": f"Cannot resend code for a {state} request.", "status": 400}
            )

        try:
            last_sent = datetime.datetime.fromisoformat(record.get("otp_last_sent_at"))
        except Exception:
            last_sent = None
        if last_sent:
            elapsed = (datetime.datetime.utcnow() - last_sent).total_seconds()
            if elapsed < OTP_RESEND_COOLDOWN_SECONDS:
                wait = int(OTP_RESEND_COOLDOWN_SECONDS - elapsed)
                return JSONResponse(
                    {
                        "message": f"Please wait {wait}s before resending.",
                        "status": 429,
                        "retry_in_seconds": wait,
                    }
                )

        otp = generate_otp(length=6)
        now_iso = datetime.datetime.utcnow().isoformat()
        new_expiry = datetime.datetime.utcnow() + datetime.timedelta(minutes=OTP_EXPIRY_MINUTES)
        withdraw_requests_collection.update_one(
            {"id": withdraw_id},
            {
                "$set": {
                    "otp_plain": otp,
                    "otp_attempts": 0,
                    "otp_expires_at": new_expiry.isoformat(),
                    "otp_last_sent_at": now_iso,
                    "updated_at": now_iso,
                }
            },
        )

        user = user_collection.find_one({"_id": user_id})
        if user:
            bank_summary = _bank_summary(record.get("bank_account") or {})
            background_tasks.add_task(
                send_withdraw_otp_email,
                user.get("email"),
                user.get("user_name", "there"),
                otp,
                float(record.get("amount", 0)),
                bank_summary,
            )

        return JSONResponse(
            {
                "status": 200,
                "message": "A new code has been sent to your email.",
                "otp_sent_to": _mask_email(user.get("email") if user else ""),
            }
        )
    except Exception as e:
        logger.error(f"Error in /withdraw/resend-otp: {e}")
        return JSONResponse({"message": str(e), "status": 500})


@router.get("/withdraw-history")
async def withdraw_history(data: dict = Depends(get_token)):
    try:
        user_id = data.get("id")
        try:
            cursor = withdraw_requests_collection.find({"user_id": user_id}).sort("created_at", -1)
            records = []
            for doc in cursor:
                records.append({
                    "id": doc.get("id"),
                    "amount": doc.get("amount"),
                    "state": doc.get("state"),
                    "bank_name": (doc.get("bank_account") or {}).get("bank_name"),
                    "account_number_last4": ((doc.get("bank_account") or {}).get("account_number") or "")[-4:],
                    "created_at": doc.get("created_at"),
                    "verified_at": doc.get("verified_at"),
                })
        except Exception as e:
            logger.error(f"Error listing withdraws: {e}")
            records = []

        return {"status": 200, "data": records}
    except Exception as e:
        logger.error(f"Error in /withdraw-history: {e}")
        return JSONResponse({"message": str(e), "status": 500})
