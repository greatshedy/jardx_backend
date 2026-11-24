"""Jard Block: public plans, authenticated investments, and admin review."""
import datetime
import logging
import os
import secrets
from typing import Optional

from dateutil.relativedelta import relativedelta
from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse

from db.database import (
    block_investments_collection,
    block_plans_collection,
    block_referrals_collection,
    block_settings_collection,
    notifications_collection,
    transactions_collection,
    user_collection,
)
from utill import get_token, send_push_notification_to_user, upload_to_cloudinary

logger = logging.getLogger("jardx")

user_router = APIRouter(prefix="/users/block", tags=["Jard Block"])
admin_router = APIRouter(prefix="/admin/block", tags=["Jard Block Admin"])

DEFAULT_SETTINGS = {
    "bank_name": "GTBank",
    "account_name": "Jarvad Supplying In'l",
    "account_number": "0106896517",
    "transfer_instructions": (
        "Transfer the exact investment amount to the account above, then upload your proof of payment. "
        "Use your full name as the transfer narration."
    ),
    "disclaimer": (
        "Jard Block is an investment product. Returns are projections based on the selected plan and are "
        "subject to the terms of your investment. Only invest funds you can commit for the full term."
    ),
    "support_whatsapp": "2347085579245",
}


def _now():
    return datetime.datetime.utcnow()


def _now_iso():
    return _now().isoformat()


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _iso(value):
    return value.isoformat() if isinstance(value, datetime.datetime) else value


def _get_settings():
    try:
        docs = list(block_settings_collection.find({}))
        if docs:
            merged = dict(DEFAULT_SETTINGS)
            merged.update({k: v for k, v in docs[0].items() if v not in (None, "")})
            return merged
    except Exception as e:
        logger.error(f"Block settings load failed: {e}")
    return dict(DEFAULT_SETTINGS)


def _find_plan(plan_id_or_slug: str):
    key = str(plan_id_or_slug or "").strip()
    if not key:
        return None
    try:
        plan = block_plans_collection.find_one({"_id": key})
        if plan:
            return plan
    except Exception:
        pass
    try:
        for plan in block_plans_collection.find({}):
            if str(plan.get("slug", "")).lower() == key.lower() or str(plan.get("_id")) == key:
                return plan
            if str(plan.get("name", "")).lower() == key.lower():
                return plan
    except Exception as e:
        logger.error(f"Block plan lookup failed: {e}")
    return None


def _public_plan(plan):
    return {
        "_id": str(plan.get("_id", "")),
        "slug": plan.get("slug", ""),
        "name": plan.get("name", ""),
        "description": plan.get("description", ""),
        "unit_price": _safe_float(plan.get("unit_price")),
        "units_per_slot": _safe_int(plan.get("units_per_slot"), 1),
        "slot_value": _safe_float(plan.get("unit_price")) * max(1, _safe_int(plan.get("units_per_slot"), 1)),
        "annual_roi": _safe_float(plan.get("annual_roi")),
        "moratorium_months": _safe_int(plan.get("moratorium_months"), 4),
        "payout_months": _safe_int(plan.get("payout_months"), 12),
        "principal_return": bool(plan.get("principal_return", True)),
        "referral_rate": _safe_float(plan.get("referral_rate")),
        "unlimited_slots": bool(plan.get("unlimited_slots", True)),
        "total_slots": plan.get("total_slots"),
        "active": bool(plan.get("active", True)),
        "order": _safe_int(plan.get("order"), 99),
        "color": plan.get("color", "#FF6900"),
        "benefits": plan.get("benefits", []),
    }


def _serialize_investment(inv):
    if not inv:
        return None
    out = dict(inv)
    out["_id"] = str(out.get("_id", ""))
    for key in ("submitted_at", "reviewed_at", "approval_date", "moratorium_ends_at", "first_payout_at", "maturity_date", "created_at", "updated_at", "rejected_at"):
        if isinstance(out.get(key), datetime.datetime):
            out[key] = out[key].isoformat()
    out["payout_ledger"] = out.get("payout_ledger") or []
    return out


def _ledger_entries(approval_date, moratorium_ends, maturity_date, monthly_return, total_invested, principal_return):
    entries = []
    for month in range(1, 13):
        due = moratorium_ends + relativedelta(months=month)
        entries.append({
            "period": month,
            "kind": "roi",
            "due_at": due.isoformat(),
            "amount": round(float(monthly_return), 2),
            "status": "pending",
            "paid_at": None,
            "paid_by": None,
            "note": "",
            "wallet_tx_ref": None,
        })
    if principal_return:
        entries.append({
            "period": "principal",
            "kind": "principal",
            "due_at": maturity_date.isoformat(),
            "amount": round(float(total_invested), 2),
            "status": "pending",
            "paid_at": None,
            "paid_by": None,
            "note": "",
            "wallet_tx_ref": None,
        })
    return entries


def _append_user_list(user_id, field, value):
    try:
        user = user_collection.find_one({"_id": user_id}) or {}
        values = list(user.get(field) or [])
        if value not in values:
            values.append(value)
            user_collection.update_one({"_id": user_id}, {"$set": {field: values}})
    except Exception as e:
        logger.error(f"Could not append {field} for {user_id}: {e}")


def _notify_user(user_id, title, body, url="/jardblock/investments", notif_type="BLOCK"):
    try:
        user = user_collection.find_one({"_id": user_id})
        if not user:
            return
        notifications_collection.insert_one({
            "user_id": user_id,
            "title": title,
            "body": body,
            "type": notif_type,
            "action_text": "View",
            "url": url,
            "is_read": False,
            "created_at": _now_iso(),
        })
        if (user.get("notification_settings") or {}).get("push", False):
            payload = {"screen": "notifications", "url": url}
            send_push_notification_to_user(user, title, body, payload)
    except Exception as e:
        logger.error(f"Block user notification failed: {e}")


def _notify_admins(title, body, url="/dashboard?view=block-investments"):
    admin_emails = [
        item.strip().lower()
        for item in os.getenv("ADMIN_EMAILS", "jarvadgroup.business@gmail.com").split(",")
        if item.strip()
    ]
    try:
        for email in admin_emails:
            admin = user_collection.find_one({"email": email})
            if not admin:
                continue
            notifications_collection.insert_one({
                "user_id": admin.get("_id"),
                "title": title,
                "body": body,
                "type": "BLOCK",
                "action_text": "Review",
                "url": url,
                "is_read": False,
                "created_at": _now_iso(),
            })
            if (admin.get("notification_settings") or {}).get("push", False):
                send_push_notification_to_user(admin, title, body, {"screen": "notifications", "url": url})
    except Exception as e:
        logger.error(f"Block admin notification failed: {e}")


def _find_investment(investment_id: str):
    try:
        inv = block_investments_collection.find_one({"_id": investment_id})
        if inv:
            return inv
    except Exception:
        pass
    try:
        for inv in block_investments_collection.find({}):
            if str(inv.get("_id")) == str(investment_id) or inv.get("ref") == investment_id:
                return inv
    except Exception as e:
        logger.error(f"Block investment lookup failed: {e}")
    return None


def _resolve_referrer(user, referral_code):
    code = str(referral_code or "").strip().upper()
    if code:
        try:
            referrer = user_collection.find_one({"referral_code": code})
            if referrer:
                return referrer
        except Exception:
            pass
    referred_by = user.get("referred_by")
    if referred_by:
        try:
            return user_collection.find_one({"_id": referred_by})
        except Exception:
            pass
    return None


def _transaction(tx_ref, user_id, amount, purpose, status, metadata):
    try:
        transactions_collection.insert_one({
            "tx_ref": tx_ref,
            "user_id": user_id,
            "amount": float(amount),
            "total_amount": float(amount),
            "gateway": "Manual Bank Transfer",
            "type": "DEBIT",
            "purpose": purpose,
            "status": status,
            "source_type": "block_investment",
            "metadata": metadata,
            "created_at": _now_iso(),
            "completed_at": _now_iso() if status == "SUCCESS" else "",
        })
    except Exception as e:
        logger.error(f"Block transaction write failed: {e}")


# ----------------------------- USER ROUTES -----------------------------

@user_router.get("/plans")
async def public_block_plans():
    try:
        plans = [_public_plan(p) for p in block_plans_collection.find({}) if p.get("active", True)]
        plans.sort(key=lambda item: (item.get("order", 99), item.get("name", "")))
        return JSONResponse({"status": 200, "data": plans, "settings": _get_settings()})
    except Exception as e:
        logger.error(f"Block plans error: {e}")
        return JSONResponse({"status": 500, "message": str(e), "data": []})


@user_router.get("/plans/{slug}")
async def public_block_plan(slug: str):
    plan = _find_plan(slug)
    if not plan or not plan.get("active", True):
        return JSONResponse(status_code=404, content={"status": 404, "message": "Plan not found"})
    return JSONResponse({"status": 200, "data": _public_plan(plan)})


@user_router.get("/config")
async def public_block_config():
    return JSONResponse({"status": 200, "data": _get_settings()})


@user_router.get("/investments")
async def my_block_investments(data: dict = Depends(get_token)):
    try:
        user = user_collection.find_one({"_id": data.get("id")})
        if not user:
            return JSONResponse(status_code=401, content={"status": 401, "message": "User not found"})

        ids = user.get("block_investment_ids") or []
        investments = []
        for investment_id in ids:
            inv = _find_investment(investment_id)
            if inv:
                investments.append(_serialize_investment(inv))

        if not investments:
            email = str(user.get("email", "")).lower()
            for inv in block_investments_collection.find({}):
                if str(inv.get("user_id")) == str(user.get("_id")) or str(inv.get("user_email", "")).lower() == email:
                    investments.append(_serialize_investment(inv))

        investments.sort(key=lambda item: item.get("submitted_at") or item.get("created_at") or "", reverse=True)
        return JSONResponse({"status": 200, "data": investments})
    except Exception as e:
        logger.error(f"Block investment list error: {e}")
        return JSONResponse({"status": 500, "message": str(e), "data": []})


@user_router.get("/investments/{investment_id}")
async def my_block_investment(investment_id: str, data: dict = Depends(get_token)):
    try:
        user = user_collection.find_one({"_id": data.get("id")})
        inv = _find_investment(investment_id)
        if not user or not inv:
            return JSONResponse(status_code=404, content={"status": 404, "message": "Investment not found"})
        if str(inv.get("user_id")) != str(user.get("_id")) and str(inv.get("user_email", "")).lower() != str(user.get("email", "")).lower():
            return JSONResponse(status_code=403, content={"status": 403, "message": "Access denied"})
        return JSONResponse({"status": 200, "data": _serialize_investment(inv), "settings": _get_settings()})
    except Exception as e:
        logger.error(f"Block investment detail error: {e}")
        return JSONResponse({"status": 500, "message": str(e)})


@user_router.get("/referrals")
async def my_block_referrals(data: dict = Depends(get_token)):
    try:
        user = user_collection.find_one({"_id": data.get("id")})
        if not user:
            return JSONResponse(status_code=401, content={"status": 401, "message": "User not found"})
        email = str(user.get("email", "")).lower()
        referrals = []
        for ref in block_referrals_collection.find({}):
            if str(ref.get("referrer_id")) == str(user.get("_id")) or str(ref.get("referrer_email", "")).lower() == email:
                referrals.append({**ref, "_id": str(ref.get("_id", ""))})
        referrals.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        total_pending = sum(float(item.get("commission_amount") or 0) for item in referrals if item.get("status") == "pending")
        total_paid = sum(float(item.get("commission_amount") or 0) for item in referrals if item.get("status") == "paid")
        return JSONResponse({
            "status": 200,
            "data": referrals,
            "summary": {"count": len(referrals), "pending": round(total_pending, 2), "paid": round(total_paid, 2)},
        })
    except Exception as e:
        logger.error(f"Block referral list error: {e}")
        return JSONResponse({"status": 500, "message": str(e), "data": []})


@user_router.post("/invest")
async def create_block_investment(
    plan_id: str = Form(...),
    slots: int = Form(...),
    full_name: str = Form(""),
    phone: str = Form(""),
    transfer_reference: str = Form(""),
    referral_code: str = Form(""),
    client_ref: str = Form(""),
    declaration: str = Form("false"),
    proof: UploadFile = File(...),
    data: dict = Depends(get_token),
):
    try:
        user = user_collection.find_one({"_id": data.get("id")})
        if not user:
            return JSONResponse(status_code=401, content={"status": 401, "message": "User not found"})
        if str(declaration).lower() not in ("true", "1", "on", "yes"):
            return JSONResponse(status_code=400, content={"status": 400, "message": "Please accept the investment declaration"})

        slots = _safe_int(slots)
        if slots <= 0:
            return JSONResponse(status_code=400, content={"status": 400, "message": "Enter a valid number of slots"})

        plan = _find_plan(plan_id)
        if not plan or not plan.get("active", True):
            return JSONResponse(status_code=400, content={"status": 400, "message": "This plan is not available"})

        plan = _public_plan(plan)
        units_per_slot = max(1, plan["units_per_slot"])
        total_units = slots * units_per_slot
        unit_price = plan["unit_price"]
        total_invested = round(total_units * unit_price, 2)
        annual_return = round(total_invested * plan["annual_roi"] / 100, 2)
        monthly_return = round(annual_return / 12, 2)

        if client_ref:
            try:
                for existing in block_investments_collection.find({}):
                    if existing.get("client_ref") == client_ref and str(existing.get("user_id")) == str(user.get("_id")):
                        return JSONResponse({"status": 200, "message": "Investment already submitted", "data": _serialize_investment(existing)})
            except Exception:
                pass

        content_type = (proof.content_type or "").lower()
        if not (content_type.startswith("image/") or content_type == "application/pdf"):
            return JSONResponse(status_code=400, content={"status": 400, "message": "Proof must be an image or PDF"})
        contents = await proof.read()
        if not contents:
            return JSONResponse(status_code=400, content={"status": 400, "message": "Proof file is empty"})
        if len(contents) > 10 * 1024 * 1024:
            return JSONResponse(status_code=400, content={"status": 400, "message": "Proof file must be under 10MB"})

        proof_url = upload_to_cloudinary(contents, folder=f"block/proofs/{user.get('_id')}")
        if not proof_url:
            return JSONResponse(status_code=500, content={"status": 500, "message": "Could not upload proof"})

        referrer = _resolve_referrer(user, referral_code)
        if referrer and str(referrer.get("_id")) == str(user.get("_id")):
            referrer = None
        referral_rate = _safe_float(plan.get("referral_rate"))
        referral_commission = round(total_invested * referral_rate, 2) if referrer else 0.0

        tx_ref = f"BLOCK-{secrets.token_hex(5).upper()}"
        now = _now()
        investment = {
            "ref": tx_ref,
            "tx_ref": tx_ref,
            "client_ref": client_ref,
            "user_id": user.get("_id"),
            "user_email": user.get("email", ""),
            "user_name": full_name.strip() or user.get("user_name", ""),
            "phone": phone.strip() or user.get("phone_number", ""),
            "plan_id": plan["_id"],
            "plan_slug": plan["slug"],
            "plan_name": plan["name"],
            "plan_snapshot": plan,
            "slots": slots,
            "units_per_slot": units_per_slot,
            "total_units": total_units,
            "unit_price": unit_price,
            "total_invested": total_invested,
            "annual_roi": plan["annual_roi"],
            "annual_return": annual_return,
            "monthly_return": monthly_return,
            "moratorium_months": plan["moratorium_months"],
            "payout_months": plan["payout_months"],
            "principal_return": plan["principal_return"],
            "status": "pending_review",
            "payment_method": "bank_transfer",
            "transfer_reference": transfer_reference.strip(),
            "proof_url": proof_url,
            "payouts_paid": 0,
            "total_paid_out": 0.0,
            "principal_paid": False,
            "payout_ledger": [],
            "referral": {
                "code": referral_code.strip().upper(),
                "referrer_id": referrer.get("_id") if referrer else None,
                "referrer_email": referrer.get("email", "") if referrer else "",
                "rate": referral_rate,
                "commission_amount": referral_commission,
                "status": "pending" if referrer and referral_commission > 0 else "none",
            },
            "submitted_at": now.isoformat(),
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "admin_note": "",
            "legacy": False,
        }
        inserted = block_investments_collection.insert_one(investment)
        investment_id = str(inserted.inserted_id)

        _append_user_list(user.get("_id"), "block_investment_ids", investment_id)
        _transaction(
            tx_ref,
            user.get("_id"),
            total_invested,
            f"Block Investment - {plan['name']} Plan",
            "PENDING",
            {
                "block_investment_id": investment_id,
                "plan_id": plan["slug"],
                "plan_name": plan["name"],
                "slots": slots,
                "total_units": total_units,
                "unit_price": unit_price,
                "isBlockInvestment": True,
                "transfer_reference": transfer_reference.strip(),
            },
        )
        _notify_admins(
            "New Jard Block investment",
            f"{investment['user_name']} submitted a {plan['name']} investment of ₦{total_invested:,.0f}.",
        )
        _notify_user(
            user.get("_id"),
            "Jard Block application received",
            f"Your {plan['name']} investment of ₦{total_invested:,.0f} is awaiting review.",
        )
        return JSONResponse({
            "status": 200,
            "message": "Investment submitted for review",
            "data": _serialize_investment({**investment, "_id": inserted.inserted_id}),
        })
    except Exception as e:
        logger.error(f"Block investment submit error: {e}")
        return JSONResponse(status_code=500, content={"status": 500, "message": str(e)})


# ----------------------------- ADMIN ROUTES -----------------------------

from routes.admin import verify_admin_token

admin_router.dependencies.append(Depends(verify_admin_token))


@admin_router.get("/summary")
async def admin_block_summary(admin: dict = Depends(verify_admin_token)):
    try:
        investments = list(block_investments_collection.find({}))
        active = [i for i in investments if i.get("status") in ("active", "moratorium", "matured")]
        total_invested = sum(float(i.get("total_invested") or 0) for i in active)
        total_paid = sum(float(i.get("total_paid_out") or 0) for i in investments)
        return JSONResponse({
            "status": 200,
            "data": {
                "total_investments": len(investments),
                "active_investments": len(active),
                "pending_review": len([i for i in investments if i.get("status") == "pending_review"]),
                "total_invested": round(total_invested, 2),
                "total_paid_out": round(total_paid, 2),
                "rejected": len([i for i in investments if i.get("status") == "rejected"]),
            },
        })
    except Exception as e:
        logger.error(f"Block admin summary error: {e}")
        return JSONResponse({"status": 500, "message": str(e)})


@admin_router.get("/plans")
async def admin_block_plans(admin: dict = Depends(verify_admin_token)):
    try:
        plans = []
        for plan in block_plans_collection.find({}):
            item = _public_plan(plan)
            item.update({
                "total_slots": plan.get("total_slots"),
                "unlimited_slots": bool(plan.get("unlimited_slots", True)),
                "created_at": plan.get("created_at"),
                "updated_at": plan.get("updated_at"),
            })
            plans.append(item)
        plans.sort(key=lambda item: (item.get("order", 99), item.get("name", "")))
        return JSONResponse({"status": 200, "data": plans})
    except Exception as e:
        logger.error(f"Block admin plans error: {e}")
        return JSONResponse({"status": 500, "message": str(e), "data": []})


@admin_router.post("/plans")
async def admin_create_block_plan(payload: dict, admin: dict = Depends(verify_admin_token)):
    try:
        slug = str(payload.get("slug") or payload.get("name") or "").strip().lower().replace(" ", "-")
        if not slug:
            return JSONResponse({"status": 400, "message": "Plan name or slug is required"})
        for plan in block_plans_collection.find({}):
            if plan.get("slug") == slug:
                return JSONResponse({"status": 400, "message": "A plan with this slug already exists"})
        now = _now_iso()
        doc = {
            "slug": slug,
            "name": payload.get("name", slug.title()),
            "description": payload.get("description", ""),
            "unit_price": _safe_float(payload.get("unit_price")),
            "units_per_slot": max(1, _safe_int(payload.get("units_per_slot"), 1)),
            "annual_roi": _safe_float(payload.get("annual_roi")),
            "moratorium_months": max(0, _safe_int(payload.get("moratorium_months"), 4)),
            "payout_months": max(1, _safe_int(payload.get("payout_months"), 12)),
            "principal_return": bool(payload.get("principal_return", True)),
            "referral_rate": _safe_float(payload.get("referral_rate")),
            "unlimited_slots": bool(payload.get("unlimited_slots", True)),
            "total_slots": payload.get("total_slots"),
            "active": bool(payload.get("active", True)),
            "order": _safe_int(payload.get("order"), 99),
            "color": payload.get("color", "#FF6900"),
            "benefits": payload.get("benefits", []),
            "created_at": now,
            "updated_at": now,
        }
        inserted = block_plans_collection.insert_one(doc)
        return JSONResponse({"status": 200, "message": "Plan created", "data": _public_plan({**doc, "_id": inserted.inserted_id})})
    except Exception as e:
        logger.error(f"Block plan create error: {e}")
        return JSONResponse({"status": 500, "message": str(e)})


@admin_router.put("/plans/{plan_id}")
async def admin_update_block_plan(plan_id: str, payload: dict, admin: dict = Depends(verify_admin_token)):
    try:
        plan = _find_plan(plan_id)
        if not plan:
            return JSONResponse({"status": 404, "message": "Plan not found"})
        updates = {}
        for key in ("slug", "name", "description", "color"):
            if key in payload:
                updates[key] = payload[key]
        for key in ("unit_price", "annual_roi", "referral_rate"):
            if key in payload:
                updates[key] = _safe_float(payload[key])
        for key in ("units_per_slot", "moratorium_months", "payout_months", "order"):
            if key in payload:
                updates[key] = _safe_int(payload[key])
        for key in ("principal_return", "unlimited_slots", "active"):
            if key in payload:
                updates[key] = bool(payload[key])
        if "total_slots" in payload:
            updates["total_slots"] = payload.get("total_slots")
        if "benefits" in payload:
            updates["benefits"] = payload.get("benefits")
        updates["updated_at"] = _now_iso()
        block_plans_collection.update_one({"_id": plan.get("_id")}, {"$set": updates})
        return JSONResponse({"status": 200, "message": "Plan updated"})
    except Exception as e:
        logger.error(f"Block plan update error: {e}")
        return JSONResponse({"status": 500, "message": str(e)})


@admin_router.delete("/plans/{plan_id}")
async def admin_delete_block_plan(plan_id: str, admin: dict = Depends(verify_admin_token)):
    try:
        plan = _find_plan(plan_id)
        if not plan:
            return JSONResponse({"status": 404, "message": "Plan not found"})
        block_plans_collection.delete_one({"_id": plan.get("_id")})
        return JSONResponse({"status": 200, "message": "Plan deleted"})
    except Exception as e:
        logger.error(f"Block plan delete error: {e}")
        return JSONResponse({"status": 500, "message": str(e)})


@admin_router.get("/settings")
async def admin_block_settings(admin: dict = Depends(verify_admin_token)):
    return JSONResponse({"status": 200, "data": _get_settings()})


@admin_router.put("/settings")
async def admin_update_block_settings(payload: dict, admin: dict = Depends(verify_admin_token)):
    try:
        updates = {key: payload[key] for key in DEFAULT_SETTINGS if key in payload}
        updates["updated_at"] = _now_iso()
        existing = list(block_settings_collection.find({}))
        if existing:
            block_settings_collection.update_one({"_id": existing[0].get("_id")}, {"$set": updates})
        else:
            block_settings_collection.insert_one({"_id": "global", **DEFAULT_SETTINGS, **updates})
        return JSONResponse({"status": 200, "message": "Settings updated", "data": _get_settings()})
    except Exception as e:
        logger.error(f"Block settings update error: {e}")
        return JSONResponse({"status": 500, "message": str(e)})


@admin_router.get("/investments")
async def admin_block_investments(
    status: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    admin: dict = Depends(verify_admin_token),
):
    try:
        items = []
        term = (search or "").lower().strip()
        for inv in block_investments_collection.find({}):
            if status and status != "ALL" and str(inv.get("status", "")).lower() != status.lower():
                continue
            if term:
                haystack = " ".join(str(inv.get(k, "")) for k in ("ref", "tx_ref", "user_name", "user_email", "plan_name", "status", "transfer_reference")).lower()
                if term not in haystack:
                    continue
            items.append(_serialize_investment(inv))
        items.sort(key=lambda item: item.get("submitted_at") or item.get("created_at") or "", reverse=True)
        page = max(1, _safe_int(page, 1))
        page_size = min(100, max(1, _safe_int(page_size, 20)))
        total = len(items)
        start = (page - 1) * page_size
        return JSONResponse({
            "status": 200,
            "data": items[start:start + page_size],
            "pagination": {"page": page, "page_size": page_size, "total_count": total, "total_pages": max(1, (total + page_size - 1) // page_size)},
        })
    except Exception as e:
        logger.error(f"Block admin list error: {e}")
        return JSONResponse({"status": 500, "message": str(e), "data": []})


@admin_router.get("/investments/{investment_id}")
async def admin_block_investment(investment_id: str, admin: dict = Depends(verify_admin_token)):
    inv = _find_investment(investment_id)
    if not inv:
        return JSONResponse({"status": 404, "message": "Investment not found"})
    return JSONResponse({"status": 200, "data": _serialize_investment(inv)})


@admin_router.post("/investments/{investment_id}/approve")
async def admin_approve_block(investment_id: str, payload: dict = None, admin: dict = Depends(verify_admin_token)):
    try:
        inv = _find_investment(investment_id)
        if not inv:
            return JSONResponse({"status": 404, "message": "Investment not found"})
        if inv.get("status") not in ("pending_review", "moratorium", "active", "matured"):
            return JSONResponse({"status": 400, "message": "This investment cannot be approved"})
        now = _now()
        moratorium_end = now + relativedelta(months=max(0, _safe_int(inv.get("moratorium_months"), 4)))
        maturity = moratorium_end + relativedelta(months=max(1, _safe_int(inv.get("payout_months"), 12)))
        ledger = _ledger_entries(now, moratorium_end, maturity, inv.get("monthly_return"), inv.get("total_invested"), inv.get("principal_return", True))
        status = "moratorium" if now < moratorium_end else "active"
        updates = {
            "status": status,
            "approval_date": now.isoformat(),
            "moratorium_ends_at": moratorium_end.isoformat(),
            "first_payout_at": (moratorium_end + relativedelta(months=1)).isoformat(),
            "maturity_date": maturity.isoformat(),
            "payout_ledger": ledger,
            "reviewed_at": now.isoformat(),
            "reviewed_by": admin.get("email", ""),
            "admin_note": (payload or {}).get("note", ""),
            "updated_at": now.isoformat(),
        }
        block_investments_collection.update_one({"_id": inv.get("_id")}, {"$set": updates})
        try:
            transactions_collection.update_one(
                {"tx_ref": inv.get("tx_ref")},
                {"$set": {"status": "SUCCESS", "completed_at": now.isoformat(), "metadata.block_investment_id": str(inv.get("_id"))}},
            )
        except Exception:
            pass
        referral = inv.get("referral") or {}
        if referral.get("referrer_id") and _safe_float(referral.get("commission_amount")) > 0:
            ref_doc = {
                "referrer_id": referral.get("referrer_id"),
                "referrer_email": referral.get("referrer_email", ""),
                "referee_id": inv.get("user_id"),
                "referee_email": inv.get("user_email", ""),
                "referee_name": inv.get("user_name", ""),
                "investment_id": str(inv.get("_id")),
                "tx_ref": inv.get("tx_ref"),
                "plan_id": inv.get("plan_slug"),
                "plan_name": inv.get("plan_name"),
                "rate": referral.get("rate"),
                "commission_amount": referral.get("commission_amount"),
                "status": "pending",
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
            ref_inserted = block_referrals_collection.insert_one(ref_doc)
            _append_user_list(referral.get("referrer_id"), "block_referral_ids", str(ref_inserted.inserted_id))
            _notify_user(
                referral.get("referrer_id"),
                "Block referral commission pending",
                f"Your referral {inv.get('user_name')} invested in {inv.get('plan_name')}. Commission is awaiting approval.",
                "/jardblock/referrals",
            )
        _notify_user(
            inv.get("user_id"),
            "Jard Block investment approved",
            f"Your {inv.get('plan_name')} investment is now {status}. First payout is scheduled after the moratorium.",
        )
        return JSONResponse({"status": 200, "message": "Investment approved", "data": _serialize_investment(_find_investment(investment_id))})
    except Exception as e:
        logger.error(f"Block approve error: {e}")
        return JSONResponse({"status": 500, "message": str(e)})


@admin_router.post("/investments/{investment_id}/reject")
async def admin_reject_block(investment_id: str, payload: dict = None, admin: dict = Depends(verify_admin_token)):
    try:
        inv = _find_investment(investment_id)
        if not inv:
            return JSONResponse({"status": 404, "message": "Investment not found"})
        note = (payload or {}).get("note", "")
        now = _now_iso()
        block_investments_collection.update_one(
            {"_id": inv.get("_id")},
            {"$set": {"status": "rejected", "admin_note": note, "rejected_at": now, "reviewed_at": now, "reviewed_by": admin.get("email", ""), "updated_at": now}},
        )
        try:
            transactions_collection.update_one({"tx_ref": inv.get("tx_ref")}, {"$set": {"status": "FAILED", "completed_at": now}})
        except Exception:
            pass
        _notify_user(
            inv.get("user_id"),
            "Jard Block application rejected",
            f"Your {inv.get('plan_name')} investment was not approved. {note or 'Please contact support for more information.'}",
        )
        return JSONResponse({"status": 200, "message": "Investment rejected"})
    except Exception as e:
        logger.error(f"Block reject error: {e}")
        return JSONResponse({"status": 500, "message": str(e)})


@admin_router.post("/investments/{investment_id}/payout")
async def admin_block_payout(investment_id: str, payload: dict, admin: dict = Depends(verify_admin_token)):
    try:
        inv = _find_investment(investment_id)
        if not inv:
            return JSONResponse({"status": 404, "message": "Investment not found"})
        period = payload.get("period")
        credit_wallet = bool(payload.get("credit_wallet", False))
        note = payload.get("note", "")
        ledger = list(inv.get("payout_ledger") or [])
        target_index = None
        for index, entry in enumerate(ledger):
            if str(entry.get("period")) == str(period):
                target_index = index
                break
        if target_index is None:
            return JSONResponse({"status": 404, "message": "Payout entry not found"})
        entry = ledger[target_index]
        if entry.get("status") == "paid":
            return JSONResponse({"status": 400, "message": "This entry is already paid"})
        amount = _safe_float(entry.get("amount"))
        now = _now_iso()
        wallet_tx_ref = None
        if credit_wallet:
            user = user_collection.find_one({"_id": inv.get("user_id")})
            if not user:
                return JSONResponse({"status": 404, "message": "Investor not found"})
            new_balance = _safe_float(user.get("wallet_balance")) + amount
            user_collection.update_one({"_id": user.get("_id")}, {"$set": {"wallet_balance": new_balance}})
            wallet_tx_ref = f"BLOCK-PAYOUT-{secrets.token_hex(5).upper()}"
            transactions_collection.insert_one({
                "tx_ref": wallet_tx_ref,
                "user_id": user.get("_id"),
                "amount": amount,
                "total_amount": amount,
                "gateway": "Wallet",
                "type": "CREDIT",
                "purpose": f"Block {'Principal Return' if period == 'principal' else 'ROI Payout'} - {inv.get('plan_name')}",
                "status": "SUCCESS",
                "source_type": "block_payout",
                "metadata": {"block_investment_id": str(inv.get("_id")), "period": period},
                "created_at": now,
                "completed_at": now,
            })
        entry.update({"status": "paid", "paid_at": now, "paid_by": admin.get("email", ""), "note": note, "wallet_tx_ref": wallet_tx_ref})
        ledger[target_index] = entry
        updates = {"payout_ledger": ledger, "updated_at": now}
        if str(period) == "principal":
            updates["principal_paid"] = True
            updates["principal_paid_at"] = now
            updates["status"] = "matured"
        else:
            paid_count = len([item for item in ledger if item.get("kind") == "roi" and item.get("status") == "paid"])
            total_paid = sum(_safe_float(item.get("amount")) for item in ledger if item.get("status") == "paid")
            updates["payouts_paid"] = paid_count
            updates["total_paid_out"] = round(total_paid, 2)
        block_investments_collection.update_one({"_id": inv.get("_id")}, {"$set": updates})
        _notify_user(
            inv.get("user_id"),
            "Block payout recorded",
            f"Your {inv.get('plan_name')} {'principal return' if period == 'principal' else 'ROI payout'} of ₦{amount:,.2f} has been marked paid.",
        )
        return JSONResponse({"status": 200, "message": "Payout recorded"})
    except Exception as e:
        logger.error(f"Block payout error: {e}")
        return JSONResponse({"status": 500, "message": str(e)})


@admin_router.get("/referrals")
async def admin_block_referrals(admin: dict = Depends(verify_admin_token)):
    try:
        items = [{**ref, "_id": str(ref.get("_id", ""))} for ref in block_referrals_collection.find({})]
        items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return JSONResponse({"status": 200, "data": items})
    except Exception as e:
        logger.error(f"Block admin referrals error: {e}")
        return JSONResponse({"status": 500, "message": str(e), "data": []})


@admin_router.post("/referrals/{referral_id}/credit")
async def admin_credit_block_referral(referral_id: str, payload: dict = None, admin: dict = Depends(verify_admin_token)):
    try:
        ref = block_referrals_collection.find_one({"_id": referral_id})
        if not ref:
            return JSONResponse({"status": 404, "message": "Referral not found"})
        if ref.get("status") == "paid":
            return JSONResponse({"status": 400, "message": "Commission already paid"})
        user = user_collection.find_one({"_id": ref.get("referrer_id")})
        if not user:
            return JSONResponse({"status": 404, "message": "Referrer not found"})
        amount = _safe_float(ref.get("commission_amount"))
        now = _now_iso()
        new_balance = _safe_float(user.get("wallet_balance")) + amount
        user_collection.update_one({"_id": user.get("_id")}, {"$set": {"wallet_balance": new_balance}})
        tx_ref = f"BLOCK-REF-{secrets.token_hex(5).upper()}"
        transactions_collection.insert_one({
            "tx_ref": tx_ref,
            "user_id": user.get("_id"),
            "amount": amount,
            "total_amount": amount,
            "gateway": "Referral System",
            "type": "CREDIT",
            "purpose": f"Block Referral Commission - {ref.get('plan_name')}",
            "status": "SUCCESS",
            "source_type": "block_referral",
            "metadata": {"block_referral_id": str(ref.get("_id")), "investment_id": ref.get("investment_id")},
            "created_at": now,
            "completed_at": now,
        })
        block_referrals_collection.update_one(
            {"_id": ref.get("_id")},
            {"$set": {"status": "paid", "paid_at": now, "paid_by": admin.get("email", ""), "wallet_tx_ref": tx_ref, "updated_at": now}},
        )
        _notify_user(
            user.get("_id"),
            "Block referral commission credited",
            f"₦{amount:,.2f} referral commission has been credited to your wallet.",
            "/jardblock/referrals",
        )
        return JSONResponse({"status": 200, "message": "Referral commission credited"})
    except Exception as e:
        logger.error(f"Block referral credit error: {e}")
        return JSONResponse({"status": 500, "message": str(e)})
