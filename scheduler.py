import datetime
import logging
import os
from dateutil.relativedelta import relativedelta
from cryptography.fernet import Fernet

from db.database import (
    user_collection, portfolio_collection, transactions_collection,
    vendors_collection, partners_collection, jard_kidz_collection,
    sub_settings_collection, notifications_collection
)
from utill import (
    send_subscription_reminder_email,
    send_subscription_penalty_email,
    send_installment_reminder_email,
    send_installment_penalty_email,
    send_jardkidz_reminder_email,
    send_jardkidz_penalty_email,
    send_subscription_auto_debit_failed_email,
)

logger = logging.getLogger("jardx")

ENCRYPTION_KEY = os.getenv("BACKEND_ENCRYPTION_KEY")
if ENCRYPTION_KEY:
    cipher = Fernet(ENCRYPTION_KEY.encode())
else:
    cipher = None

def _encrypt_pin(pin: str) -> str:
    if not cipher:
        return pin
    return cipher.encrypt(pin.encode()).decode()

def _decrypt_pin(encrypted: str) -> str:
    if not cipher:
        return encrypted
    try:
        return cipher.decrypt(encrypted.encode()).decode()
    except Exception:
        return ""

def get_settings():
    doc = sub_settings_collection.find_one({})
    if not doc:
        doc = {
            "testing_mode": False,
            "testing_duration_minutes": 3,
            "vendor_fee": 25000,
            "partner_fee": 100000,
            "sub_duration_months": 1,
            "penalty_percentage": 5
        }
    return doc

def _duration_delta(settings, base_months=1):
    if settings.get("testing_mode"):
        return datetime.timedelta(minutes=settings.get("testing_duration_minutes", 3))
    return relativedelta(months=base_months)

def _now():
    return datetime.datetime.utcnow()

def _is_due(target_date_iso, settings):
    if not target_date_iso:
        return False
    try:
        target = datetime.datetime.fromisoformat(target_date_iso)
        return _now() >= target
    except Exception:
        return False

def _days_between(from_iso, to_iso):
    try:
        f = datetime.datetime.fromisoformat(from_iso)
        t = datetime.datetime.fromisoformat(to_iso)
        return (t - f).days
    except Exception:
        return None

def _time_remaining(target_iso, settings):
    if not target_iso:
        return None
    try:
        target = datetime.datetime.fromisoformat(target_iso)
        diff = target - _now()
        if settings.get("testing_mode"):
            return diff.total_seconds() / 60
        return diff.days
    except Exception:
        return None

def _mark_reminder_sent(collection, doc_id, reminder_key):
    try:
        collection.update_one({"_id": doc_id}, {"$set": {reminder_key: True}})
    except Exception as e:
        logger.error(f"Failed to mark reminder {reminder_key} for {doc_id}: {e}")

def _mark_reminder_sent_filter(collection, filter_query, reminder_key):
    try:
        collection.update_one(filter_query, {"$set": {reminder_key: True}})
    except Exception as e:
        logger.error(f"Failed to mark reminder {reminder_key}: {e}")

def _send_email_safe(email_func, *args, **kwargs):
    try:
        email_func(*args, **kwargs)
    except Exception as e:
        logger.error(f"Email send failed: {e}")

def _deduct_wallet(user_id, amount, purpose, portfolio_id=None, months_covered=0, penalty=0):
    try:
        user = user_collection.find_one({"_id": user_id})
        if not user:
            return False
        balance = float(user.get("wallet_balance", 0))
        total = amount + penalty
        if balance < total:
            return False
        new_balance = balance - total
        user_collection.update_one({"_id": user_id}, {"$set": {"wallet_balance": new_balance}})
        tx_ref = f"AUTO-{secrets.token_hex(6).upper()}"
        tx = {
            "tx_ref": tx_ref,
            "user_id": user_id,
            "portfolio_id": portfolio_id or "",
            "amount": total,
            "fee_part": amount,
            "penalty_part": penalty,
            "gateway": "Wallet",
            "type": "DEBIT",
            "purpose": purpose,
            "status": "SUCCESS",
            "created_at": _now().isoformat(),
            "months_covered": months_covered,
            "auto_debit": True
        }
        transactions_collection.insert_one(tx)
        notifications_collection.insert_one({
            "user_id": user_id,
            "title": "Auto-Debit Successful",
            "body": f"₦{total:,.2f} auto-deducted{f' (incl. ₦{penalty:,.2f} penalty)' if penalty else ''}. {purpose}",
            "type": "PAYMENT",
            "action_text": "View",
            "created_at": _now().isoformat(),
            "is_read": False
        })
        logger.info(f"Auto-debit success: user={user_id}, amount={total}, penalty={penalty}, purpose={purpose}")
        return True
    except Exception as e:
        logger.error(f"Auto-debit deduction failed for {user_id}: {e}")
        return False

import secrets


# ─────────────────────────────────────────────
# AUTO-DEBIT TASKS
# ─────────────────────────────────────────────

def auto_debit_portfolios():
    settings = get_settings()
    items = list(portfolio_collection.find({
        "auto_debit": True,
        "status": "Active",
        "encrypted_pin": {"$ne": ""}
    }))
    for item in items:
        try:
            if not _is_due(item.get("next_payment_date"), settings):
                continue
            user_id = item["user_id"]
            monthly = float(item.get("monthly_payment", 0))
            penalty = float(item.get("penalty_amount", 0))
            total_due = monthly + penalty
            months_paid = item.get("months_paid", 0)
            duration = item.get("duration_months", 1)
            if months_paid >= duration:
                continue
            purpose = f"Auto Installment: {item.get('house_name', 'Property')}"
            ok = _deduct_wallet(user_id, monthly, purpose, str(item["_id"]), 1, penalty)
            if ok:
                new_months = months_paid + 1
                remaining = float(item.get("remaining_balance", 0)) - monthly
                status = "Completed" if new_months >= duration or remaining <= 0.5 else "Active"
                try:
                    curr = datetime.datetime.fromisoformat(item.get("next_payment_date", _now().isoformat()))
                    nxt = curr + relativedelta(months=1)
                except Exception:
                    nxt = _now() + relativedelta(months=1)
                portfolio_collection.update_one(
                    {"_id": item["_id"]},
                    {"$set": {
                        "months_paid": new_months,
                        "remaining_balance": max(0, remaining),
                        "amount_paid": float(item.get("amount_paid", 0)) + monthly,
                        "status": status,
                        "next_payment_date": nxt.isoformat(),
                        "penalty_amount": 0
                    }}
                )
            else:
                portfolio_collection.update_one(
                    {"_id": item["_id"]},
                    {"$set": {"auto_debit": False, "last_auto_debit_failed": _now().isoformat()}}
                )
                user = user_collection.find_one({"_id": user_id})
                email = user.get("email", "") if user else ""
                if email:
                    _send_email_safe(
                        send_installment_reminder_email,
                        email, user.get("user_name", "User"),
                        f"{item.get('house_name', 'Property')} - Installment",
                        monthly, penalty, is_penalty=True
                    )
                _send_email_safe(
                    send_installment_auto_debit_failed_email,
                    email, user.get("user_name", "User") if user else "User",
                    item.get("house_name", "Property"), monthly + penalty
                )
        except Exception as e:
            logger.error(f"auto_debit_portfolios item {item.get('_id')}: {e}")

def auto_debit_jardkidz():
    settings = get_settings()
    items = list(jard_kidz_collection.find({
        "auto_debit": True,
        "status": "Active",
        "encrypted_pin": {"$ne": ""}
    }))
    for item in items:
        try:
            if not item.get("next_payment_date") or not _is_due(item["next_payment_date"], settings):
                continue
            user_id = item["user_id"]
            monthly = float(item.get("monthly_amount", 0))
            penalty = float(item.get("penalty_amount", 0))
            months_paid = item.get("months_paid", 0)
            total_months = item.get("total_months", 1)
            if months_paid >= total_months:
                continue
            purpose = f"Auto JardKidz Top-Up: {item.get('child_name', 'Child')}"
            ok = _deduct_wallet(user_id, monthly, purpose, months_covered=1, penalty=penalty)
            if ok:
                new_months = months_paid + 1
                status = "Completed" if new_months >= total_months else "Active"
                try:
                    curr = datetime.datetime.fromisoformat(item["next_payment_date"])
                    nxt = curr + relativedelta(months=1)
                except Exception:
                    nxt = _now() + relativedelta(months=1)
                jard_kidz_collection.update_one(
                    {"_id": item["_id"]},
                    {"$set": {
                        "months_paid": new_months,
                        "status": status,
                        "next_payment_date": nxt.isoformat(),
                        "penalty_amount": 0
                    }}
                )
            else:
                jard_kidz_collection.update_one(
                    {"_id": item["_id"]},
                    {"$set": {"auto_debit": False, "last_auto_debit_failed": _now().isoformat()}}
                )
                user = user_collection.find_one({"_id": user_id})
                email = user.get("email", "") if user else ""
                if email:
                    _send_email_safe(
                        send_jardkidz_penalty_email,
                        email, user.get("user_name", "User"),
                        item.get("child_name", "Child"), monthly, penalty
                    )
        except Exception as e:
            logger.error(f"auto_debit_jardkidz item {item.get('_id')}: {e}")

def auto_debit_vendors():
    settings = get_settings()
    items = list(vendors_collection.find({
        "auto_debit": True,
        "encrypted_pin": {"$ne": ""}
    }))
    for item in items:
        try:
            if not _is_due(item.get("subscription_expiry"), settings):
                continue
            user_id = item["user_id"]
            fee = float(settings.get("vendor_fee", 25000))
            if item.get("subscription_status") == "expired":
                continue
            purpose = "Auto Vendor Subscription Renewal"
            ok = _deduct_wallet(user_id, fee, purpose)
            if ok:
                delta = _duration_delta(settings)
                if isinstance(delta, datetime.timedelta):
                    new_expiry = (_now() + delta).isoformat()
                else:
                    new_expiry = (_now() + delta).isoformat()
                vendors_collection.update_one(
                    {"_id": item["_id"]},
                    {"$set": {
                        "subscription_expiry": new_expiry,
                        "subscription_status": "active",
                        "subscription_start": _now().isoformat(),
                        "reminder_30d_sent": False,
                        "reminder_14d_sent": False,
                        "reminder_7d_sent": False,
                        "reminder_3d_sent": False,
                        "last_auto_debit_failed": None
                    }}
                )
            else:
                vendors_collection.update_one(
                    {"_id": item["_id"]},
                    {"$set": {"auto_debit": False, "last_auto_debit_failed": _now().isoformat()}}
                )
                user = user_collection.find_one({"_id": user_id})
                email = user.get("email", "") if user else ""
                if email:
                    _send_email_safe(
                        send_subscription_auto_debit_failed_email,
                        email, user.get("user_name", "User"),
                        "Vendor", fee
                    )
        except Exception as e:
            logger.error(f"auto_debit_vendors item {item.get('_id')}: {e}")

def auto_debit_partners():
    settings = get_settings()
    items = list(partners_collection.find({
        "auto_debit": True,
        "encrypted_pin": {"$ne": ""}
    }))
    for item in items:
        try:
            if not _is_due(item.get("subscription_expiry"), settings):
                continue
            user_id = item["user_id"]
            fee = float(settings.get("partner_fee", 100000))
            if item.get("subscription_status") == "expired":
                continue
            purpose = "Auto Partner Subscription Renewal"
            ok = _deduct_wallet(user_id, fee, purpose)
            if ok:
                delta = _duration_delta(settings)
                new_expiry = (_now() + delta).isoformat()
                partners_collection.update_one(
                    {"_id": item["_id"]},
                    {"$set": {
                        "subscription_expiry": new_expiry,
                        "subscription_status": "active",
                        "subscription_start": _now().isoformat(),
                        "reminder_30d_sent": False,
                        "reminder_14d_sent": False,
                        "reminder_7d_sent": False,
                        "reminder_3d_sent": False,
                        "last_auto_debit_failed": None
                    }}
                )
            else:
                partners_collection.update_one(
                    {"_id": item["_id"]},
                    {"$set": {"auto_debit": False, "last_auto_debit_failed": _now().isoformat()}}
                )
                user = user_collection.find_one({"_id": user_id})
                email = user.get("email", "") if user else ""
                if email:
                    _send_email_safe(
                        send_subscription_auto_debit_failed_email,
                        email, user.get("user_name", "User"),
                        "Partner", fee
                    )
        except Exception as e:
            logger.error(f"auto_debit_partners item {item.get('_id')}: {e}")


# ─────────────────────────────────────────────
# REMINDER TASKS
# ─────────────────────────────────────────────

def send_reminders():
    settings = get_settings()

    # === Installment Reminders (7d, 3d) ===
    portfolios = list(portfolio_collection.find({"status": "Active"}))
    for p in portfolios:
        try:
            nxt = p.get("next_payment_date")
            if not nxt:
                continue
            user = user_collection.find_one({"_id": p["user_id"]})
            if not user:
                continue
            email = user.get("email", "")
            name = user.get("user_name", "User")
            house = p.get("house_name", "Property")
            monthly = float(p.get("monthly_payment", 0))
            penalty = float(p.get("penalty_amount", 0))

            remaining = _time_remaining(nxt, settings)
            if remaining is None:
                continue

            if 6.5 <= remaining <= 7.5 and not p.get("reminder_7d_sent"):
                _send_email_safe(send_installment_reminder_email, email, name, house, monthly, penalty, days=7)
                _mark_reminder_sent(portfolio_collection, p["_id"], "reminder_7d_sent")

            if 2.5 <= remaining <= 3.5 and not p.get("reminder_3d_sent"):
                _send_email_safe(send_installment_reminder_email, email, name, house, monthly, penalty, days=3)
                _mark_reminder_sent(portfolio_collection, p["_id"], "reminder_3d_sent")
        except Exception as e:
            logger.error(f"send_reminders portfolio {p.get('_id')}: {e}")

    # === JardKidz Reminders (7d, 3d) ===
    kidz = list(jard_kidz_collection.find({"status": "Active"}))
    for k in kidz:
        try:
            nxt = k.get("next_payment_date")
            if not nxt:
                continue
            user = user_collection.find_one({"_id": k["user_id"]})
            if not user:
                continue
            email = user.get("email", "")
            name = user.get("user_name", "User")
            child = k.get("child_name", "Child")
            monthly = float(k.get("monthly_amount", 0))
            penalty = float(k.get("penalty_amount", 0))

            remaining = _time_remaining(nxt, settings)
            if remaining is None:
                continue

            if 6.5 <= remaining <= 7.5 and not k.get("reminder_7d_sent"):
                _send_email_safe(send_jardkidz_reminder_email, email, name, child, monthly, penalty, days=7)
                _mark_reminder_sent(jard_kidz_collection, k["_id"], "reminder_7d_sent")

            if 2.5 <= remaining <= 3.5 and not k.get("reminder_3d_sent"):
                _send_email_safe(send_jardkidz_reminder_email, email, name, child, monthly, penalty, days=3)
                _mark_reminder_sent(jard_kidz_collection, k["_id"], "reminder_3d_sent")
        except Exception as e:
            logger.error(f"send_reminders jardkidz {k.get('_id')}: {e}")

    # === Vendor Reminders (30d, 14d, 7d, 3d) ===
    vendors = list(vendors_collection.find({"subscription_status": {"$ne": "expired"}}))
    for v in vendors:
        try:
            expiry = v.get("subscription_expiry")
            start = v.get("subscription_start")
            if not expiry:
                continue
            user = user_collection.find_one({"_id": v["user_id"]})
            if not user:
                continue
            email = user.get("email", "")
            name = user.get("user_name", "User")

            remaining = _time_remaining(expiry, settings)
            if remaining is None:
                continue

            total_duration = _days_between(start, expiry) if start else None
            if total_duration is not None and total_duration < 45:
                if remaining <= 14:
                    pass
                else:
                    continue

            if 29.5 <= remaining <= 30.5 and not v.get("reminder_30d_sent"):
                _send_email_safe(send_subscription_reminder_email, email, name, "Vendor", settings.get("vendor_fee", 25000), days=30)
                _mark_reminder_sent(vendors_collection, v["_id"], "reminder_30d_sent")

            if 13.5 <= remaining <= 14.5 and not v.get("reminder_14d_sent"):
                _send_email_safe(send_subscription_reminder_email, email, name, "Vendor", settings.get("vendor_fee", 25000), days=14)
                _mark_reminder_sent(vendors_collection, v["_id"], "reminder_14d_sent")

            if 6.5 <= remaining <= 7.5 and not v.get("reminder_7d_sent"):
                _send_email_safe(send_subscription_reminder_email, email, name, "Vendor", settings.get("vendor_fee", 25000), days=7)
                _mark_reminder_sent(vendors_collection, v["_id"], "reminder_7d_sent")

            if 2.5 <= remaining <= 3.5 and not v.get("reminder_3d_sent"):
                _send_email_safe(send_subscription_reminder_email, email, name, "Vendor", settings.get("vendor_fee", 25000), days=3)
                _mark_reminder_sent(vendors_collection, v["_id"], "reminder_3d_sent")
        except Exception as e:
            logger.error(f"send_reminders vendor {v.get('_id')}: {e}")

    # === Partner Reminders (30d, 14d, 7d, 3d) ===
    partners = list(partners_collection.find({"subscription_status": {"$ne": "expired"}}))
    for p in partners:
        try:
            expiry = p.get("subscription_expiry")
            start = p.get("subscription_start")
            if not expiry:
                continue
            user = user_collection.find_one({"_id": p["user_id"]})
            if not user:
                continue
            email = user.get("email", "")
            name = user.get("user_name", "User")

            remaining = _time_remaining(expiry, settings)
            if remaining is None:
                continue

            total_duration = _days_between(start, expiry) if start else None
            if total_duration is not None and total_duration < 45:
                if remaining <= 14:
                    pass
                else:
                    continue

            if 29.5 <= remaining <= 30.5 and not p.get("reminder_30d_sent"):
                _send_email_safe(send_subscription_reminder_email, email, name, "Partner", settings.get("partner_fee", 100000), days=30)
                _mark_reminder_sent(partners_collection, p["_id"], "reminder_30d_sent")

            if 13.5 <= remaining <= 14.5 and not p.get("reminder_14d_sent"):
                _send_email_safe(send_subscription_reminder_email, email, name, "Partner", settings.get("partner_fee", 100000), days=14)
                _mark_reminder_sent(partners_collection, p["_id"], "reminder_14d_sent")

            if 6.5 <= remaining <= 7.5 and not p.get("reminder_7d_sent"):
                _send_email_safe(send_subscription_reminder_email, email, name, "Partner", settings.get("partner_fee", 100000), days=7)
                _mark_reminder_sent(partners_collection, p["_id"], "reminder_7d_sent")

            if 2.5 <= remaining <= 3.5 and not p.get("reminder_3d_sent"):
                _send_email_safe(send_subscription_reminder_email, email, name, "Partner", settings.get("partner_fee", 100000), days=3)
                _mark_reminder_sent(partners_collection, p["_id"], "reminder_3d_sent")
        except Exception as e:
            logger.error(f"send_reminders partner {p.get('_id')}: {e}")


# ─────────────────────────────────────────────
# PENALTY TASKS
# ─────────────────────────────────────────────

def apply_penalties():
    settings = get_settings()
    penalty_pct = float(settings.get("penalty_percentage", 5)) / 100.0

    # Installments: past due, not auto_debit, or auto_debit failed
    portfolios = list(portfolio_collection.find({"status": "Active"}))
    for p in portfolios:
        try:
            nxt = p.get("next_payment_date")
            if not nxt or not _is_due(nxt, settings):
                continue
            if p.get("auto_debit") and not p.get("last_auto_debit_failed"):
                continue
            monthly = float(p.get("monthly_payment", 0))
            current_penalty = float(p.get("penalty_amount", 0))
            if current_penalty >= monthly:
                continue
            new_penalty = current_penalty + round(monthly * penalty_pct, 2)
            portfolio_collection.update_one(
                {"_id": p["_id"]},
                {"$set": {"penalty_amount": new_penalty, "last_penalty_applied": _now().isoformat()}}
            )
            user = user_collection.find_one({"_id": p["user_id"]})
            if user and user.get("email"):
                _send_email_safe(
                    send_installment_penalty_email,
                    user["email"], user.get("user_name", "User"),
                    p.get("house_name", "Property"), monthly, new_penalty
                )
            logger.info(f"Penalty applied: portfolio {p['_id']}, new_penalty={new_penalty}")
        except Exception as e:
            logger.error(f"apply_penalties portfolio {p.get('_id')}: {e}")

    # JardKidz: past due
    kidz = list(jard_kidz_collection.find({"status": "Active"}))
    for k in kidz:
        try:
            nxt = k.get("next_payment_date")
            if not nxt or not _is_due(nxt, settings):
                continue
            if k.get("auto_debit") and not k.get("last_auto_debit_failed"):
                continue
            monthly = float(k.get("monthly_amount", 0))
            current_penalty = float(k.get("penalty_amount", 0))
            if current_penalty >= monthly:
                continue
            new_penalty = current_penalty + round(monthly * penalty_pct, 2)
            jard_kidz_collection.update_one(
                {"_id": k["_id"]},
                {"$set": {"penalty_amount": new_penalty, "last_penalty_applied": _now().isoformat()}}
            )
            user = user_collection.find_one({"_id": k["user_id"]})
            if user and user.get("email"):
                _send_email_safe(
                    send_jardkidz_penalty_email,
                    user["email"], user.get("user_name", "User"),
                    k.get("child_name", "Child"), monthly, new_penalty
                )
            logger.info(f"Penalty applied: jardkidz {k['_id']}, new_penalty={new_penalty}")
        except Exception as e:
            logger.error(f"apply_penalties jardkidz {k.get('_id')}: {e}")


def run_all():
    logger.info("Scheduler: Running all tasks...")
    try:
        auto_debit_portfolios()
    except Exception as e:
        logger.error(f"Scheduler auto_debit_portfolios error: {e}")
    try:
        auto_debit_jardkidz()
    except Exception as e:
        logger.error(f"Scheduler auto_debit_jardkidz error: {e}")
    try:
        auto_debit_vendors()
    except Exception as e:
        logger.error(f"Scheduler auto_debit_vendors error: {e}")
    try:
        auto_debit_partners()
    except Exception as e:
        logger.error(f"Scheduler auto_debit_partners error: {e}")
    try:
        send_reminders()
    except Exception as e:
        logger.error(f"Scheduler send_reminders error: {e}")
    try:
        apply_penalties()
    except Exception as e:
        logger.error(f"Scheduler apply_penalties error: {e}")
    logger.info("Scheduler: All tasks completed")
