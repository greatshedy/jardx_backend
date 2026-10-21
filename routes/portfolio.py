from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from fastapi.responses import JSONResponse
from db.database import user_collection, house_collection, portfolio_collection, transactions_collection, sub_settings_collection, housing_subscriptions_collection
from model import PropertyPurchase, PortfolioModel
from utill import get_token, send_purchase_email, reassemble_base64_string, process_referral_logic, process_partner_commission, VerifyHashed, get_image_url
from scheduler import get_settings, _encrypt_pin
import datetime
import math
import secrets
from dateutil.relativedelta import relativedelta
import logging

logger = logging.getLogger("jardx")

router = APIRouter(prefix="/users/portfolio", tags=["Portfolio"])

def apply_promo_price(house, price):
    if promo_is_active(house) and house.get("promo_applies_to") in (None, "", "unit_price", "both"):
        if house["house_promo_type"] == "percentage":
            discount = float(house.get("house_promo_value", 0))
            return round(price * (1 - discount / 100), 2)
        elif house["house_promo_type"] == "fixed":
            return float(house.get("house_promo_value", 0))
    return price


def _parse_dt(value):
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def promo_is_active(house, now=None):
    if not house.get("house_is_promo") or not house.get("house_promo_type"):
        return False
    now = now or datetime.datetime.utcnow()
    starts_at = _parse_dt(house.get("promo_starts_at"))
    ends_at = _parse_dt(house.get("promo_ends_at"))
    if starts_at and now < starts_at:
        return False
    if ends_at and now > ends_at:
        return False
    return True


def _promo_applies_to(house, target):
    applies_to = house.get("promo_applies_to") or "unit_price"
    return applies_to in (target, "both")


def _apply_promo_amount(house, amount, target):
    if not promo_is_active(house) or not _promo_applies_to(house, target):
        return round(float(amount), 2)
    promo_type = house.get("house_promo_type")
    promo_value = max(0.0, float(house.get("house_promo_value", 0) or 0))
    if promo_type == "percentage":
        discount = min(100.0, promo_value)
        return round(max(0.0, float(amount) * (1 - (discount / 100))), 2)
    if promo_type == "fixed":
        return round(max(0.0, min(float(amount), promo_value)), 2)
    return round(float(amount), 2)


def build_promo_snapshot(house, unit_price, registration_fee):
    active = promo_is_active(house)
    snapshot = {
        "promo_id": f"{house.get('_id')}-{house.get('promo_starts_at') or 'always'}",
        "is_active_at_enrollment": active,
        "label": house.get("promo_label") or "",
        "terms": house.get("promo_terms") or "",
        "discount_type": house.get("house_promo_type") if active else "",
        "discount_value": float(house.get("house_promo_value", 0) or 0) if active else 0.0,
        "applies_to": house.get("promo_applies_to") or "unit_price",
        "starts_at": house.get("promo_starts_at") or "",
        "ends_at": house.get("promo_ends_at") or "",
        "original_unit_price": round(float(unit_price), 2),
        "locked_unit_price": _apply_promo_amount(house, unit_price, "unit_price"),
        "original_registration_fee": round(float(registration_fee), 2),
        "locked_registration_fee": _apply_promo_amount(house, registration_fee, "registration_fee"),
        "locked_at": datetime.datetime.utcnow().isoformat(),
    }
    return snapshot


def normalize_property_type(value):
    normalized = (value or "").strip().lower().replace("_", " ").replace("-", " ")
    if normalized in {"residential", "residential property"}:
        return "residential"
    if normalized in {"residential commercial", "residential commercial property"}:
        return "residential-commercial"
    if normalized in {"full commercial", "commercial", "full commercial property"}:
        return "full_commercial"
    return normalized.replace(" ", "-")


def get_property_type_price(house, property_type):
    key = normalize_property_type(property_type)
    prices = house.get("property_type_prices") or {}
    candidates = [key, key.replace("-", "_"), property_type]
    for candidate in candidates:
        try:
            value = float(prices.get(candidate))
            if value > 0:
                return value
        except (TypeError, ValueError):
            continue

    plans = house.get("house_pricing_plan") or []
    for plan in plans:
        plan_type = normalize_property_type(plan.get("propertyType") or plan.get("property_type"))
        if plan_type == key:
            try:
                value = float(plan.get("outrightPrice") or plan.get("price"))
                if value > 0:
                    return value
            except (TypeError, ValueError):
                pass

    if plans:
        try:
            value = float(plans[0].get("outrightPrice") or plans[0].get("price"))
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    return 0.0


def resolve_housing_fee(house, settings):
    value = house.get("housing_registration_fee")
    if value not in (None, ""):
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            pass
    try:
        return max(0.0, float(settings.get("housing_registration_fee", 100000)))
    except (TypeError, ValueError):
        return 100000.0


def resolve_housing_multiplier(house, settings):
    value = house.get("housing_savings_multiplier")
    if value not in (None, ""):
        try:
            parsed = float(value)
            if parsed > 0:
                return parsed
        except (TypeError, ValueError):
            pass
    try:
        parsed = float(settings.get("housing_savings_multiplier", 2.0))
        return parsed if parsed > 0 else 2.0
    except (TypeError, ValueError):
        return 2.0


def calculate_required_contribution(target_amount, duration_months, frequency):
    target = float(target_amount or 0)
    months = max(1, int(duration_months or 1))
    freq = (frequency or "Monthly").strip().lower()
    if freq == "daily":
        divisor = months * 30
    elif freq == "weekly":
        divisor = months * 4
    elif freq == "yearly":
        divisor = max(1, months / 12)
    else:
        divisor = months
    return int(math.ceil(target / divisor)) if divisor > 0 else int(math.ceil(target))


@router.post("/buy")
def buy_property(purchase: PropertyPurchase, background_tasks: BackgroundTasks, data: dict = Depends(get_token)):
    try:
        user_id = data.get("id")
        if not user_id:
            return JSONResponse(status_code=401, content={"message": "Invalid user session"})
        
        user = user_collection.find_one({"_id": user_id})
        house = house_collection.find_one({"_id": purchase.house_id})

        if not user or not house:
            return JSONResponse(status_code=404, content={"message": "User or House not found"})
        
        # Check if user has enough balance
        current_balance = float(user.get("wallet_balance", 0.0))
        if current_balance < purchase.amount_to_pay:
            return JSONResponse(status_code=400, content={"message": "Insufficient wallet balance. Please top up your wallet."})

        # Determine the correct plan index
        plan_idx = purchase.plan_index if purchase.plan_index is not None else 0
        pricing_plans = house.get("house_pricing_plan", [])
        if plan_idx >= len(pricing_plans):
            logger.error(f"Plan index {plan_idx} out of range for house {purchase.house_id} (has {len(pricing_plans)} plans)")
            return JSONResponse(status_code=400, content={"message": "Selected plan is not available."})
        house_plan = pricing_plans[plan_idx]

        # Process Outright vs Installment
        if purchase.plan_type == "outright":
            # outright purchase uses the price
            outright_price = apply_promo_price(house, float(house_plan["outrightPrice"]))
            
            # create portfolio logic
            portfolio_item = {
                "user_id": user_id,
                "house_id": purchase.house_id,
                "house_name": house["house_name"],
                "plan_type": purchase.plan_type,
                "total_price": outright_price,
                "amount_paid": purchase.amount_to_pay,
                "remaining_balance": 0.0,
                "monthly_payment": 0.0,
                "duration_months": 0,
                "months_paid": 0,
                "next_payment_date": "",
                "status": "Completed",
                "house_image": house.get("house_image", [])[0] if house.get("house_image") else "",
                "created_at": datetime.datetime.utcnow().isoformat(),
                "auto_debit": False,
                "encrypted_pin": "",
                "penalty_amount": 0.0,
                "reminder_7d_sent": False,
                "reminder_3d_sent": False
            }

        
        else:
            # Installment Handling
            if purchase.plan_index is None:
                return JSONResponse(status_code=400, content={"message": "Installment plan index missing."})

            installments = house_plan.get("numInstallments", [])
            if purchase.plan_index >= len(installments):
                logger.error(f"Installment index {purchase.plan_index} out of range for plan {plan_idx} (has {len(installments)} options)")
                return JSONResponse(status_code=400, content={"message": "Selected installment option is not available."})

            num_months = int(installments[purchase.plan_index])
            percentage_increase = float(house_plan["percentageIncrease"])
            outright_price = apply_promo_price(house, float(house_plan["outrightPrice"]))
            down_payment_perc = float(house_plan["downPayment"])

            total_percentage_increase = (percentage_increase / 100) * (purchase.plan_index + 1)
            total_payment = outright_price + (outright_price * total_percentage_increase)
            
            expected_downpayment = total_payment * (down_payment_perc / 100)
            remaining_balance = total_payment - purchase.amount_to_pay
            monthly_payment = remaining_balance / num_months

            next_payment_date = (datetime.datetime.utcnow() + relativedelta(months=1)).isoformat()

            portfolio_item = {
                "user_id": user_id,
                "house_id": purchase.house_id,
                "house_name": house["house_name"],
                "plan_type": purchase.plan_type,
                "total_price": total_payment,
                "amount_paid": purchase.amount_to_pay,
                "remaining_balance": remaining_balance,
                "monthly_payment": monthly_payment,
                "duration_months": num_months,
                "months_paid": 0,
                "next_payment_date": next_payment_date,
                "status": "Active",
                "house_image": house.get("house_image", [])[0] if house.get("house_image") else "",
                "created_at": datetime.datetime.utcnow().isoformat(),
                "auto_debit": False,
                "encrypted_pin": "",
                "penalty_amount": 0.0,
                "reminder_7d_sent": False,
                "reminder_3d_sent": False
            }


        # Deduct wallet balance (atomic)
        user_collection.update_one(
            {"_id": user_id, "wallet_balance": {"$gte": purchase.amount_to_pay}},
            {"$inc": {"wallet_balance": -purchase.amount_to_pay}}
        )
        
        updated_user = user_collection.find_one({"_id": user_id})
        actual_balance = float(updated_user["wallet_balance"])

        # Record Portfolio Items
        result = portfolio_collection.insert_one(portfolio_item)
        portfolio_id = str(result.inserted_id)

        # Record Transaction for History
        transactions_collection.insert_one({
            "tx_ref": f"BUY-{secrets.token_hex(6).upper()}",
            "user_id": user_id,
            "portfolio_id": portfolio_id, # Link to portfolio
            "amount": purchase.amount_to_pay,
            "gateway": "Wallet",
            "type": "DEBIT",
            "purpose": f"Purchase: {house['house_name']}",
            "unit_sqm": house_plan.get("unitSqm", "N/A"),
            "house_image": "", 
            "status": "SUCCESS",
            "created_at": datetime.datetime.utcnow().isoformat(),
            "months_covered": 0 # Initial payment
        })

        
        logger.info(f"Purchase completed: user={user_id}, house={house['house_name']}, plan={purchase.plan_type}")


        # Send Email
        image_to_send = ""
        if house.get("house_image") and len(house["house_image"]) > 0:
            first_img = house["house_image"][0]
            if isinstance(first_img, list):
                # Legacy base64 chunks
                image_to_send = "".join(first_img)
            else:
                # URL path - convert to full absolute URL for email
                from utill import get_image_url
                image_to_send = get_image_url(first_img)


        background_tasks.add_task(
            send_purchase_email,
            user.get("email"),
            user.get("user_name"),
            house["house_name"],
            purchase.plan_type,
            purchase.amount_to_pay,
            portfolio_item["remaining_balance"],
            image_to_send
        )

        # Process Referral Activation (activation only, no one-time bonus for house buys)
        process_referral_logic(user_id, purchase.amount_to_pay, user_collection, transactions_collection, give_referrer_bonus=False)
        # Partner commission (10% direct, 5% indirect for partner referrers)
        process_partner_commission(user_id, purchase.amount_to_pay, user_collection, transactions_collection)
        
        return JSONResponse(status_code=200, content={"message": "Purchase successful! Your portfolio has been updated.", "wallet_balance": actual_balance})
        
    except Exception as e:
        logger.error(f"Purchase Error: {e}")
        return JSONResponse(status_code=500, content={"message": "An error occurred during purchase. Please try again."})

@router.post("/jardhouz/save")
def create_jardhouz_saving(saving_data: dict, background_tasks: BackgroundTasks, data: dict = Depends(get_token)):
    try:
        user_id = data.get("id")
        if not user_id:
            return JSONResponse(status_code=401, content={"message": "Invalid user"})

        user = user_collection.find_one({"_id": user_id})
        estate_name = (saving_data.get("estate") or "").strip()
        estate_id = saving_data.get("estate_id")
        market_mode = saving_data.get("market_mode") or "land_estate"
        property_type = saving_data.get("property_type") or ""
        apartment_type = saving_data.get("apartment_type") or property_type
        target_amount = float(saving_data.get("target_amount", 0) or 0)
        duration = int(saving_data.get("duration", 0) or 0)
        frequency = saving_data.get("frequency") or "Monthly"
        first_payment = float(saving_data.get("amount_to_pay", 0) or 0)

        if not user or first_payment <= 0:
            return JSONResponse(status_code=400, content={"message": "Invalid data or insufficient payment"})

        house = None
        if estate_id:
            house = house_collection.find_one({"_id": estate_id})
        if not house and estate_name:
            house = house_collection.find_one({"house_name": estate_name})
        if not house:
            return JSONResponse(status_code=404, content={"message": "Estate not found"})

        if not estate_name:
            estate_name = house.get("house_name", "Estate")

        if market_mode == "land_estate" and house.get("land_enabled") is False:
            return JSONResponse(status_code=400, content={"message": "Land/Estate plans are not available for this estate"})

        if market_mode == "land_estate" and property_type:
            priced_target = get_property_type_price(house, property_type)
            if priced_target > 0:
                target_amount = priced_target
            if not apartment_type:
                apartment_type = property_type

        promo_snapshot = None
        if property_type and promo_is_active(house):
            snapshot = build_promo_snapshot(house, target_amount, 0)
            target_amount = snapshot["locked_unit_price"]
            promo_snapshot = snapshot

        if target_amount <= 0 or duration <= 0:
            return JSONResponse(status_code=400, content={"message": "Invalid target amount or duration"})

        recurring_payment = first_payment
        if property_type:
            required_payment = calculate_required_contribution(target_amount, duration, frequency)
            if first_payment < required_payment:
                return JSONResponse(status_code=400, content={
                    "message": f"First payment must be at least ₦{required_payment:,.2f}"
                })
            recurring_payment = required_payment

        current_balance = float(user.get("wallet_balance", 0.0))
        if current_balance < first_payment:
            return JSONResponse(status_code=400, content={"message": "Insufficient wallet balance"})

        house_image = house.get("house_image", [])[0] if house.get("house_image") else ""
        if isinstance(house_image, list):
            house_image = reassemble_base64_string(house_image)
        elif house_image:
            house_image = get_image_url(house_image)

        portfolio_item = {
            "user_id": user_id,
            "house_name": estate_name,
            "house_id": str(house["_id"]),
            "estate_id": str(house["_id"]),
            "market_mode": market_mode,
            "property_type": property_type,
            "apartment_type": apartment_type,
            "unit_type": apartment_type,
            "plan_type": "JardHouz Saving",
            "total_price": target_amount,
            "amount_paid": first_payment,
            "remaining_balance": max(0, target_amount - first_payment),
            "duration_months": duration,
            "frequency": frequency,
            "monthly_payment": recurring_payment,
            "is_jardhouz_saving": True,
            "promo_snapshot": promo_snapshot,
            "status": "Active",
            "house_image": house_image,
            "created_at": datetime.datetime.utcnow().isoformat(),
            "next_payment_date": (datetime.datetime.utcnow() + relativedelta(months=1)).isoformat(),
        }

        result = portfolio_collection.insert_one(portfolio_item)
        portfolio_id = str(result.inserted_id)
        user_collection.update_one({"_id": user_id}, {"$set": {"wallet_balance": current_balance - first_payment}})

        transactions_collection.insert_one({
            "tx_ref": f"JH-SAVE-{secrets.token_hex(4).upper()}",
            "user_id": user_id,
            "portfolio_id": portfolio_id,
            "amount": first_payment,
            "gateway": "Wallet",
            "type": "DEBIT",
            "purpose": f"JardHouz {market_mode.replace('_', ' ').title()} Saving: {estate_name}",
            "status": "SUCCESS",
            "created_at": datetime.datetime.utcnow().isoformat(),
            "months_covered": 1,
        })

        return JSONResponse(status_code=200, content={"message": "Saving plan started successfully!", "portfolio_id": portfolio_id})

    except Exception as e:
        logger.error(f"JH Save Error: {e}")
        return JSONResponse(status_code=500, content={"message": "Could not start saving plan"})

@router.get("/housing/config")
def get_housing_config(data: dict = Depends(get_token)):
    try:
        settings = get_settings()
        return JSONResponse(status_code=200, content={
            "message": "Housing configuration fetched",
            "status": 200,
            "data": {
                "registration_fee": resolve_housing_fee({}, settings),
                "savings_multiplier": resolve_housing_multiplier({}, settings),
            },
        })
    except Exception as e:
        logger.error(f"Housing config error: {e}")
        return JSONResponse(status_code=500, content={"message": str(e)})


@router.post("/housing/register")
def register_housing(payload: dict, background_tasks: BackgroundTasks, data: dict = Depends(get_token)):
    try:
        user_id = data.get("id")
        if not user_id:
            return JSONResponse(status_code=401, content={"message": "Invalid user"})

        user = user_collection.find_one({"_id": user_id})
        if not user:
            return JSONResponse(status_code=404, content={"message": "User not found"})

        estate_id = payload.get("estate_id")
        house = house_collection.find_one({"_id": estate_id})
        if not house:
            return JSONResponse(status_code=404, content={"message": "Estate not found"})
        if not house.get("housing_enabled"):
            return JSONResponse(status_code=400, content={"message": "Housing is not available for this estate"})
        if house.get("house_status") not in (None, "", "Active"):
            return JSONResponse(status_code=400, content={"message": "This estate is not currently available"})

        client_ref = (payload.get("client_ref") or "").strip()
        if client_ref:
            existing_sub = next(
                (
                    record for record in housing_subscriptions_collection.find({})
                    if str(record.get("user_id")) == str(user_id)
                    and record.get("client_ref") == client_ref
                ),
                None,
            )
            if existing_sub:
                existing_sub["_id"] = str(existing_sub["_id"])
                current_user = user_collection.find_one({"_id": user_id}) or user
                return JSONResponse(status_code=200, content={
                    "message": "Housing registration successful",
                    "status": 200,
                    "data": existing_sub,
                    "new_balance": float(current_user.get("wallet_balance", 0) or 0),
                })

        unit_types = house.get("housing_unit_types") or []
        unit_type_id = payload.get("unit_type_id")
        selected_unit = None
        selected_index = None
        for index, unit in enumerate(unit_types):
            unit_id = unit.get("id")
            if str(unit_id) == str(unit_type_id) or str(index) == str(unit_type_id):
                selected_unit = unit
                selected_index = index
                break

        if not selected_unit:
            return JSONResponse(status_code=400, content={"message": "Please select a valid housing option"})
        if selected_unit.get("enabled") is False:
            return JSONResponse(status_code=400, content={"message": "This housing option is not available"})

        custom_label = (payload.get("custom_label") or "").strip()
        unit_name = selected_unit.get("name") or "Housing Option"
        try:
            unit_price = float(selected_unit.get("price") or 0)
        except (TypeError, ValueError):
            unit_price = 0.0

        if selected_unit.get("is_custom"):
            if not custom_label:
                return JSONResponse(status_code=400, content={"message": "Please describe your custom structure"})
            try:
                custom_price = float(payload.get("custom_price") or 0)
            except (TypeError, ValueError):
                custom_price = 0.0
            if custom_price <= 0:
                return JSONResponse(status_code=400, content={"message": "Enter a valid custom structure value"})
            unit_price = custom_price
            unit_name = custom_label

        if unit_price <= 0:
            return JSONResponse(status_code=400, content={"message": "This housing option has no price configured"})

        settings = get_settings()
        base_fee = resolve_housing_fee(house, settings)
        multiplier = resolve_housing_multiplier(house, settings)
        promo_snapshot = build_promo_snapshot(house, unit_price, base_fee)
        locked_unit_price = promo_snapshot["locked_unit_price"]
        locked_fee = promo_snapshot["locked_registration_fee"]
        if locked_unit_price <= 0:
            return JSONResponse(status_code=400, content={"message": "Invalid promo pricing for this housing option"})
        required_savings = int(math.ceil(locked_unit_price / multiplier))

        expected_unit_price = payload.get("expected_unit_price")
        if expected_unit_price not in (None, ""):
            try:
                if abs(float(expected_unit_price) - locked_unit_price) > 0.01:
                    return JSONResponse(status_code=400, content={
                        "message": "Pricing changed. Please review the updated price and try again."
                    })
            except (TypeError, ValueError):
                pass

        already_registered = bool(user.get("housing_registration_paid"))
        fee_due = 0.0 if already_registered else locked_fee

        expected_fee = payload.get("expected_registration_fee")
        if expected_fee not in (None, ""):
            try:
                if abs(float(expected_fee) - fee_due) > 0.01:
                    return JSONResponse(status_code=400, content={
                        "message": "Pricing changed. Please review the updated registration fee and try again."
                    })
            except (TypeError, ValueError):
                pass

        stored_pin = user.get("transaction_pin")
        if fee_due > 0:
            if not stored_pin:
                return JSONResponse(status_code=400, content={"message": "No transaction PIN set"})
            try:
                if not VerifyHashed(stored_pin, str(payload.get("pin") or "")):
                    return JSONResponse(status_code=400, content={"message": "Invalid transaction PIN"})
            except Exception:
                return JSONResponse(status_code=400, content={"message": "Invalid transaction PIN"})

        current_balance = float(user.get("wallet_balance", 0) or 0)
        if current_balance < fee_due:
            return JSONResponse(status_code=400, content={"message": "Insufficient wallet balance for registration fee"})

        now = datetime.datetime.utcnow().isoformat()
        subscription_id = f"HSG-{secrets.token_hex(4).upper()}"
        house_image = house.get("house_image", [])[0] if house.get("house_image") else ""
        if isinstance(house_image, list):
            house_image = reassemble_base64_string(house_image)
        elif house_image:
            house_image = get_image_url(house_image)

        subscription = {
            "_id": subscription_id,
            "user_id": user_id,
            "subscription_id": subscription_id,
            "client_ref": client_ref,
            "estate_id": str(house.get("_id")),
            "house_name": house.get("house_name", "Estate"),
            "house_location": house.get("house_location", ""),
            "house_image": house_image,
            "user_name": user.get("user_name", ""),
            "user_email": user.get("email", ""),
            "user_phone": user.get("phone_number", ""),
            "unit_type_id": str(selected_unit.get("id") if selected_unit.get("id") is not None else selected_index),
            "unit_type_name": unit_name,
            "unit_type_index": selected_index,
            "is_custom": bool(selected_unit.get("is_custom")),
            "custom_label": custom_label if selected_unit.get("is_custom") else "",
            "original_unit_price": round(unit_price, 2),
            "locked_unit_price": round(locked_unit_price, 2),
            "required_savings": required_savings,
            "savings_multiplier": multiplier,
            "registration_fee_original": round(base_fee, 2),
            "registration_fee_locked": round(locked_fee, 2),
            "registration_fee_paid": round(fee_due, 2),
            "registration_fee_waived": already_registered,
            "promo_snapshot": promo_snapshot,
            "total_saved": 0.0,
            "access_limit": 0.0,
            "status": "Active",
            "created_at": now,
            "updated_at": now,
        }

        if fee_due > 0:
            new_balance = current_balance - fee_due
            user_collection.update_one({"_id": user_id}, {"$set": {
                "wallet_balance": new_balance,
                "is_housing_registered": True,
                "housing_registration_paid": True,
                "housing_registered_at": now,
                "housing_registration_fee_paid": fee_due,
            }})
        else:
            user_collection.update_one({"_id": user_id}, {"$set": {
                "is_housing_registered": True,
                "housing_registration_paid": True,
                "housing_registered_at": user.get("housing_registered_at") or now,
                "housing_registration_fee_paid": user.get("housing_registration_fee_paid", fee_due),
            }})

        try:
            housing_subscriptions_collection.insert_one(subscription)
        except Exception:
            rollback = {
                "is_housing_registered": user.get("is_housing_registered", False),
                "housing_registration_paid": user.get("housing_registration_paid", False),
                "housing_registered_at": user.get("housing_registered_at"),
                "housing_registration_fee_paid": user.get("housing_registration_fee_paid", 0),
            }
            if fee_due > 0:
                rollback["wallet_balance"] = current_balance
            user_collection.update_one({"_id": user_id}, {"$set": rollback})
            raise

        if fee_due > 0:
            transactions_collection.insert_one({
                "tx_ref": f"HSG-FEE-{secrets.token_hex(4).upper()}",
                "user_id": user_id,
                "subscription_id": subscription_id,
                "amount": fee_due,
                "gateway": "Wallet",
                "type": "DEBIT",
                "purpose": f"Housing Registration Fee: {house.get('house_name', 'Estate')}",
                "status": "SUCCESS",
                "created_at": now,
            })

        background_tasks.add_task(
            send_purchase_email,
            user.get("email"),
            user.get("user_name"),
            house.get("house_name", "Estate"),
            f"housing registration - {unit_name}",
            fee_due,
            0,
            house_image,
        )

        subscription["_id"] = str(subscription.get("_id", ""))
        subscription["registration_fee_due"] = round(fee_due, 2)
        subscription["new_balance"] = float(current_balance - fee_due)
        return JSONResponse(status_code=200, content={
            "message": "Housing registration successful",
            "status": 200,
            "data": subscription,
            "new_balance": float(current_balance - fee_due),
        })
    except Exception as e:
        logger.error(f"Housing registration error: {e}")
        return JSONResponse(status_code=500, content={"message": str(e)})


@router.post("/housing/save")
def create_housing_saving(payload: dict, background_tasks: BackgroundTasks, data: dict = Depends(get_token)):
    try:
        user_id = data.get("id")
        if not user_id:
            return JSONResponse(status_code=401, content={"message": "Invalid user"})

        subscription_id = payload.get("subscription_id")
        subscription = housing_subscriptions_collection.find_one({"_id": subscription_id})
        if not subscription or str(subscription.get("user_id")) != str(user_id):
            return JSONResponse(status_code=404, content={"message": "Housing subscription not found"})
        if subscription.get("status") != "Active":
            return JSONResponse(status_code=400, content={"message": "This housing subscription is not active"})

        existing_plan = next(
            (
                item for item in portfolio_collection.find({"user_id": user_id})
                if item.get("housing_subscription_id") == subscription_id
            ),
            None,
        )
        if existing_plan:
            return JSONResponse(status_code=200, content={
                "message": "Housing saving plan already started",
                "status": 200,
                "portfolio_id": str(existing_plan.get("_id")),
                "new_balance": float((user_collection.find_one({"_id": user_id}) or {}).get("wallet_balance", 0) or 0),
            })

        user = user_collection.find_one({"_id": user_id})
        if not user:
            return JSONResponse(status_code=404, content={"message": "User not found"})

        stored_pin = user.get("transaction_pin")
        if stored_pin:
            try:
                if not VerifyHashed(stored_pin, str(payload.get("pin") or "")):
                    return JSONResponse(status_code=400, content={"message": "Invalid transaction PIN"})
            except Exception:
                return JSONResponse(status_code=400, content={"message": "Invalid transaction PIN"})

        duration = int(payload.get("duration", 0) or 0)
        frequency = payload.get("frequency") or "Monthly"
        if duration <= 0:
            return JSONResponse(status_code=400, content={"message": "Enter a valid duration"})
        if frequency not in {"Daily", "Weekly", "Monthly", "Yearly"}:
            return JSONResponse(status_code=400, content={"message": "Select a valid payment frequency"})

        target_amount = float(subscription.get("required_savings", 0) or 0)
        if target_amount <= 0:
            target_amount = float(subscription.get("locked_unit_price", 0) or 0) / float(subscription.get("savings_multiplier", 1) or 1)
        if target_amount <= 0:
            return JSONResponse(status_code=400, content={"message": "Invalid housing savings target"})
        required_payment = calculate_required_contribution(target_amount, duration, frequency)
        first_payment = float(payload.get("amount_to_pay", required_payment) or required_payment)

        if first_payment < required_payment:
            return JSONResponse(status_code=400, content={
                "message": f"First payment must be at least ₦{required_payment:,.2f}"
            })

        current_balance = float(user.get("wallet_balance", 0) or 0)
        if current_balance < first_payment:
            return JSONResponse(status_code=400, content={"message": "Insufficient wallet balance for first payment"})

        multiplier = float(subscription.get("savings_multiplier", 1) or 1)
        now = datetime.datetime.utcnow().isoformat()
        portfolio_item = {
            "user_id": user_id,
            "house_name": subscription.get("house_name", "Estate"),
            "house_id": subscription.get("estate_id"),
            "estate_id": subscription.get("estate_id"),
            "market_mode": "housing",
            "housing_subscription_id": subscription_id,
            "unit_type_id": subscription.get("unit_type_id"),
            "unit_type_name": subscription.get("unit_type_name"),
            "unit_type": subscription.get("unit_type_name"),
            "plan_type": "Housing Saving",
            "total_price": target_amount,
            "structure_value": float(subscription.get("locked_unit_price", 0) or 0),
            "amount_paid": first_payment,
            "remaining_balance": max(0, target_amount - first_payment),
            "duration_months": duration,
            "frequency": frequency,
            "monthly_payment": required_payment,
            "is_jardhouz_saving": True,
            "savings_multiplier": multiplier,
            "access_limit": round(first_payment * multiplier, 2),
            "promo_snapshot": subscription.get("promo_snapshot"),
            "status": "Active",
            "house_image": subscription.get("house_image", ""),
            "created_at": now,
            "next_payment_date": (datetime.datetime.utcnow() + relativedelta(months=1)).isoformat(),
        }

        result = portfolio_collection.insert_one(portfolio_item)
        portfolio_id = str(result.inserted_id)
        user_collection.update_one({"_id": user_id}, {"$set": {"wallet_balance": current_balance - first_payment}})
        housing_subscriptions_collection.update_one(
            {"_id": subscription_id},
            {"$set": {
                "saving_plan_id": portfolio_id,
                "portfolio_id": portfolio_id,
                "total_saved": first_payment,
                "access_limit": round(first_payment * multiplier, 2),
                "duration_months": duration,
                "frequency": frequency,
                "monthly_payment": required_payment,
                "updated_at": now,
            }},
        )

        transactions_collection.insert_one({
            "tx_ref": f"HSG-SAVE-{secrets.token_hex(4).upper()}",
            "user_id": user_id,
            "portfolio_id": portfolio_id,
            "housing_subscription_id": subscription_id,
            "amount": first_payment,
            "gateway": "Wallet",
            "type": "DEBIT",
            "purpose": f"Housing Saving: {subscription.get('house_name', 'Estate')} - {subscription.get('unit_type_name', '')}",
            "status": "SUCCESS",
            "created_at": now,
            "months_covered": 1,
        })

        background_tasks.add_task(
            send_purchase_email,
            user.get("email"),
            user.get("user_name"),
            subscription.get("house_name", "Estate"),
            f"housing saving - {subscription.get('unit_type_name', '')}",
            first_payment,
            portfolio_item["remaining_balance"],
            subscription.get("house_image", ""),
        )

        return JSONResponse(status_code=200, content={
            "message": "Housing saving plan started successfully",
            "status": 200,
            "portfolio_id": portfolio_id,
            "new_balance": float(current_balance - first_payment),
        })
    except Exception as e:
        logger.error(f"Housing saving error: {e}")
        return JSONResponse(status_code=500, content={"message": str(e)})


@router.get("/housing/dashboard")
def get_housing_dashboard(data: dict = Depends(get_token)):
    try:
        user_id = data.get("id")
        if not user_id:
            return JSONResponse(status_code=401, content={"message": "Invalid user"})

        settings = get_settings()
        default_multiplier = resolve_housing_multiplier({}, settings)
        subscriptions = [
            sub for sub in housing_subscriptions_collection.find({})
            if str(sub.get("user_id")) == str(user_id)
        ]
        portfolios = [
            item for item in portfolio_collection.find({"user_id": user_id})
            if item.get("market_mode") == "housing"
        ]

        saved_by_subscription = {}
        total_saved = 0.0
        for item in portfolios:
            amount = float(item.get("amount_paid", 0) or 0)
            total_saved += amount
            sub_id = item.get("housing_subscription_id")
            if sub_id:
                saved_by_subscription[sub_id] = saved_by_subscription.get(sub_id, 0.0) + amount

        subscription_data = []
        total_access_limit = 0.0
        for sub in subscriptions:
            sub_id = sub.get("subscription_id")
            saved = saved_by_subscription.get(sub_id, float(sub.get("total_saved", 0) or 0))
            multiplier = float(sub.get("savings_multiplier", default_multiplier) or default_multiplier)
            access_limit = saved * multiplier
            total_access_limit += access_limit
            required_savings = float(sub.get("required_savings", 0) or 0)
            sub["_id"] = str(sub.get("_id"))
            sub["total_saved"] = round(saved, 2)
            sub["access_limit"] = round(access_limit, 2)
            sub["remaining_to_unlock"] = round(max(0, required_savings - saved), 2)
            sub["is_unlocked"] = access_limit >= float(sub.get("locked_unit_price", 0) or 0)
            subscription_data.append(sub)

        if not total_access_limit and total_saved:
            total_access_limit = total_saved * default_multiplier

        eligible_estates = []
        houses = list(house_collection.find({}))
        for house in houses:
            if not house.get("housing_enabled"):
                continue
            if house.get("house_status") not in (None, "", "Active"):
                continue
            multiplier = resolve_housing_multiplier(house, settings)
            estate_access = total_saved * multiplier
            options = []
            for index, unit in enumerate(house.get("housing_unit_types") or []):
                if unit.get("enabled") is False:
                    continue
                try:
                    price = float(unit.get("price") or 0)
                except (TypeError, ValueError):
                    price = 0.0
                if price <= 0:
                    continue
                option_promo = build_promo_snapshot(house, price, 0)
                locked_price = option_promo["locked_unit_price"]
                required_savings = int(math.ceil(locked_price / multiplier))
                if estate_access >= locked_price:
                    options.append({
                        "id": str(unit.get("id") if unit.get("id") is not None else index),
                        "name": unit.get("name"),
                        "price": round(locked_price, 2),
                        "original_price": round(price, 2),
                        "required_savings": required_savings,
                        "is_custom": bool(unit.get("is_custom")),
                    })
            if options:
                image = house.get("house_image", [])[0] if house.get("house_image") else ""
                if isinstance(image, list):
                    image = reassemble_base64_string(image)
                elif image:
                    image = get_image_url(image)
                eligible_estates.append({
                    "_id": str(house.get("_id")),
                    "house_name": house.get("house_name"),
                    "house_location": house.get("house_location"),
                    "house_image": image,
                    "savings_multiplier": multiplier,
                    "options": options,
                })

        return JSONResponse(status_code=200, content={
            "message": "Housing dashboard fetched",
            "status": 200,
            "data": {
                "total_saved": round(total_saved, 2),
                "total_access_limit": round(total_access_limit, 2),
                "default_multiplier": default_multiplier,
                "subscriptions": subscription_data,
                "eligible_estates": eligible_estates,
            },
        })
    except Exception as e:
        logger.error(f"Housing dashboard error: {e}")
        return JSONResponse(status_code=500, content={"message": str(e)})


@router.get("/my-portfolio")
def get_portfolio(data: dict = Depends(get_token)):
    try:
        user_id = data.get("id")
        if not user_id:
            return JSONResponse(status_code=401, content={"message": "Invalid user"})
        
        portfolio_data = list(portfolio_collection.find({"user_id": user_id}))
        from utill import get_image_url
        # Convert ObjectIds to strings and resolve images
        for item in portfolio_data:
            item["_id"] = str(item["_id"])
            
            # If house_image is missing or legacy, try to fetch current URL from house collection
            h_img = item.get("house_image")
            if not h_img or isinstance(h_img, list):
                h = house_collection.find_one({"_id": item.get("house_id")})
                if h and h.get("house_image") and len(h["house_image"]) > 0:
                    first_img = h["house_image"][0]
                    if isinstance(first_img, str) and not first_img.startswith("data:"):
                        item["house_image"] = get_image_url(first_img)
                    else:
                        item["house_image"] = ""
                else:
                    item["house_image"] = ""
            elif isinstance(h_img, str) and not h_img.startswith("data:"):
                item["house_image"] = get_image_url(h_img)
            
        return JSONResponse(status_code=200, content={"message": "Success", "data": portfolio_data})


    except Exception as e:
        logger.error(f"Portfolio Fetch Error: {e}")
        return JSONResponse(status_code=500, content={"message": "Could not fetch portfolio"})

@router.get("/estate/{portfolio_id}")
def get_portfolio_item(portfolio_id: str, data: dict = Depends(get_token)):
    try:
        user_id = data.get("id")
        if not user_id:
            return JSONResponse(status_code=401, content={"message": "Invalid user"})
        
        item = portfolio_collection.find_one({"_id": portfolio_id, "user_id": user_id})
        if not item:
            return JSONResponse(status_code=404, content={"message": "Estate not found in your portfolio"})
        
        item["_id"] = str(item["_id"])
        
        # Fetch latest 5 transactions for this portfolio
        history = list(transactions_collection.find(
            {"portfolio_id": portfolio_id, "user_id": user_id},
            sort={"created_at": -1},
            limit=5
        ))
        for h_item in history:
            h_item["_id"] = str(h_item["_id"])
        
        item["payment_history"] = history
        
        # Resolve Image
        from utill import get_image_url
        h_img = item.get("house_image")
        if not h_img or isinstance(h_img, list):
            h = house_collection.find_one({"_id": item.get("house_id")})
            if h and h.get("house_image") and len(h["house_image"]) > 0:
                first_img = h["house_image"][0]
                if isinstance(first_img, str) and not first_img.startswith("data:"):
                    item["house_image"] = get_image_url(first_img)
                else:
                    item["house_image"] = ""
            else:
                item["house_image"] = ""
        elif isinstance(h_img, str) and not h_img.startswith("data:"):
            item["house_image"] = get_image_url(h_img)
            
        return JSONResponse(status_code=200, content={"message": "Success", "data": item})

    except Exception as e:
        logger.error(f"Portfolio Item Fetch Error: {e}")
        return JSONResponse(status_code=500, content={"message": "Could not fetch estate details"})

@router.post("/toggle-auto-debit/{portfolio_id}")
def toggle_auto_debit(portfolio_id: str, payload: dict, data: dict = Depends(get_token)):
    try:
        user_id = data.get("id")
        if not user_id:
            return JSONResponse(status_code=401, content={"message": "Invalid user"})
        portfolio = portfolio_collection.find_one({"_id": portfolio_id, "user_id": user_id})
        if not portfolio:
            return JSONResponse(status_code=404, content={"message": "Portfolio not found"})
        enable = payload.get("enable", False)
        pin = payload.get("pin", "")
        if enable:
            if not pin or len(str(pin)) != 4:
                return JSONResponse(status_code=400, content={"message": "Valid 4-digit PIN required"})
            from utill import VerifyHashed
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
            portfolio_collection.update_one(
                {"_id": portfolio_id},
                {"$set": {"auto_debit": True, "encrypted_pin": encrypted}}
            )
            return JSONResponse(status_code=200, content={"message": "Auto-debit enabled", "status": 200})
        else:
            portfolio_collection.update_one(
                {"_id": portfolio_id},
                {"$set": {"auto_debit": False, "encrypted_pin": ""}}
            )
            return JSONResponse(status_code=200, content={"message": "Auto-debit disabled", "status": 200})
    except Exception as e:
        logger.error(f"Toggle auto-debit error: {e}")
        return JSONResponse(status_code=500, content={"message": str(e)})


@router.post("/pay-installment/{portfolio_id}")
def pay_installment(portfolio_id: str, background_tasks: BackgroundTasks, payload: dict = None, data: dict = Depends(get_token)):
    try:
        user_id = data.get("id")
        if not user_id:
            return JSONResponse(status_code=401, content={"message": "Invalid user"})

        user = user_collection.find_one({"_id": user_id})
        portfolio = portfolio_collection.find_one({"_id": portfolio_id, "user_id": user_id})

        if not portfolio or portfolio["status"] == "Completed":
            return JSONResponse(status_code=404, content={"message": "Active portfolio not found"})

        if portfolio.get("housing_subscription_id"):
            subscription = housing_subscriptions_collection.find_one({"_id": portfolio.get("housing_subscription_id")})
            if not subscription or subscription.get("status") != "Active":
                return JSONResponse(status_code=400, content={"message": "This housing subscription is not active"})

        # Get months to pay from payload, default to 1
        num_months = 1
        if payload and "months_paid" in payload:
            num_months = int(payload["months_paid"])
        elif payload and "months" in payload: # Fallback for different naming
            num_months = int(payload["months"])

        monthly_base = float(portfolio.get("monthly_payment", 0))
        penalty = float(portfolio.get("penalty_amount", 0))
        total_due = (monthly_base * num_months) + penalty
        current_balance = float(user.get("wallet_balance", 0.0))

        if current_balance < total_due:
            return JSONResponse(status_code=400, content={"message": f"Insufficient balance. You need ₦{total_due:,.2f} (incl. ₦{penalty:,.2f} penalty) but have ₦{current_balance:,.2f}"})

        # Calculate everything BEFORE updating any records
        try:
            # Use .get() to avoid KeyError if 'months_paid' is missing
            current_months_paid = portfolio.get("months_paid", 0)
            new_months_paid = current_months_paid + num_months
            
            current_amount_paid = float(portfolio.get("amount_paid", 0))
            new_amount_paid = current_amount_paid + total_due
            
            remaining_balance = float(portfolio.get("remaining_balance", 0)) - total_due
            
            # Status check
            status = "Completed" if new_months_paid >= portfolio.get("duration_months", 24) or remaining_balance <= 0.5 else "Active"
            
            # Robust date parsing
            next_date = ""
            if status == "Active":
                try:
                    # Increment next payment date by the number of months paid
                    current_next_date = datetime.datetime.fromisoformat(portfolio.get("next_payment_date", datetime.datetime.utcnow().isoformat()))
                    next_date = (current_next_date + relativedelta(months=num_months)).isoformat()
                except (ValueError, TypeError, KeyError):
                    next_date = (datetime.datetime.utcnow() + relativedelta(months=num_months)).isoformat()

            # 1. Update portfolio
            portfolio_collection.update_one(
                {"_id": portfolio_id},
                {"$set": {
                    "months_paid": new_months_paid,
                    "remaining_balance": max(0, remaining_balance),
                    "amount_paid": new_amount_paid,
                    "status": status,
                    "next_payment_date": next_date,
                    "penalty_amount": 0,
                    "access_limit": round(new_amount_paid * float(portfolio.get("savings_multiplier", 1) or 1), 2),
                }}
            )

            if portfolio.get("housing_subscription_id"):
                housing_subscriptions_collection.update_one(
                    {"_id": portfolio.get("housing_subscription_id")},
                    {"$set": {
                        "total_saved": round(new_amount_paid, 2),
                        "access_limit": round(new_amount_paid * float(portfolio.get("savings_multiplier", 1) or 1), 2),
                        "status": status,
                        "updated_at": datetime.datetime.utcnow().isoformat(),
                    }},
                )

            # 2. Record Transaction
            transactions_collection.insert_one({
                "tx_ref": f"PAY-{secrets.token_hex(6).upper()}",
                "user_id": user_id,
                "portfolio_id": portfolio_id,
                "amount": monthly_base * num_months,
                "penalty_part": penalty,
                "total_amount": total_due,
                "gateway": "Wallet",
                "type": "DEBIT",
                "purpose": f"Installment ({num_months} mo): {portfolio['house_name']}",
                "unit_sqm": portfolio.get("unit_sqm", "N/A"),
                "house_image": "",
                "status": "SUCCESS",
                "created_at": datetime.datetime.utcnow().isoformat(),
                "months_covered": num_months
            })
            
            # 3. Update wallet (atomic)
            user_collection.update_one(
                {"_id": user_id, "wallet_balance": {"$gte": total_due}},
                {"$inc": {"wallet_balance": -total_due}}
            )
            
            updated_user = user_collection.find_one({"_id": user_id})
            actual_balance = float(updated_user["wallet_balance"])

            # 4. Partner commission on installment payments
            process_partner_commission(user_id, total_due, user_collection, transactions_collection)

            # 5. Send receipt email
            background_tasks.add_task(
                send_purchase_email,
                user.get("email"),
                user.get("user_name"),
                portfolio["house_name"],
                f"installment ({num_months} months)",
                total_due,
                remaining_balance,
                ""
            )

            return JSONResponse(status_code=200, content={"message": "Installment payment successful!", "wallet_balance": actual_balance})

        except Exception as inner_e:
            logger.error(f"Logic Error in Pay Installment (Step failure): {inner_e}")
            return JSONResponse(status_code=500, content={"message": f"Internal logic error: {str(inner_e)}"})

    except Exception as e:
        logger.error(f"Pay Installment Error: {e}")
        return JSONResponse(status_code=500, content={"message": "Could not process installment payment"})
