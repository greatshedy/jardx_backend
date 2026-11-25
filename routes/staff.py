import datetime
import json
import logging
from typing import List

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse

from db.database import house_collection, products_collection, sub_settings_collection, user_collection
from utill import (
    VerifyHashed,
    create_access_token,
    delete_from_cloudinary,
    get_image_url,
    get_token,
    hashedpassword,
    reassemble_base64_string,
)
from utils.house_service import (
    normalize_pricing_plans,
    notify_new_estate,
    optional_float,
    safe_json,
    save_house_image,
)
from utils.product_service import (
    notify_new_product,
    notify_updated_product,
    save_product_image,
)

logger = logging.getLogger("jardx")

router = APIRouter(prefix="/staff", tags=["Staff"])

STAFF_ROLE = "staff"
PERM_ESTATES = "estates.manage"
PERM_PRODUCTS = "products.manage"
PERM_SUPPORT = "settings.support"
ALLOWED_PERMISSIONS = {PERM_ESTATES, PERM_PRODUCTS, PERM_SUPPORT}
SUPPORT_WHATSAPP_DEFAULT = "2347085579245"


def _response(message, code=200, **extra):
    body = {"message": message, "status": code}
    body.update(extra)
    return JSONResponse(body, status_code=code)


def _public_user(user):
    return {
        "_id": str(user.get("_id", "")),
        "user_name": user.get("user_name", ""),
        "email": user.get("email", ""),
        "phone_number": user.get("phone_number", ""),
        "profile_pic": user.get("profile_pic", ""),
        "role": user.get("role", ""),
        "permissions": list(user.get("permissions") or []),
        "must_change_password": bool(user.get("must_change_password")),
    }


def _find_user_by_email(email):
    email = str(email or "").strip()
    if not email:
        return None
    user = user_collection.find_one({"email": email})
    if not user:
        user = user_collection.find_one({"email": email.lower()})
    if not user:
        user = user_collection.find_one({"email": email.upper()})
    return user


def _require_staff(payload, permission=None, allow_password_change=False):
    user_id = (payload or {}).get("id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user = user_collection.find_one({"_id": user_id})
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account not found")

    if user.get("role") != STAFF_ROLE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Staff access required",
        )
    if user.get("status") == "Blocked" or user.get("staff_active") is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Staff account is disabled",
        )
    if user.get("must_change_password") and not allow_password_change:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password change required",
        )
    if permission and permission not in (user.get("permissions") or []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to perform this action",
        )
    return user


def staff_access(permission=None, allow_password_change=False):
    def dependency(payload: dict = Depends(get_token)):
        return _require_staff(payload, permission, allow_password_change)

    return dependency


# ---------------------------------------------------------------------------
# Auth / profile
# ---------------------------------------------------------------------------

@router.post("/login")
async def staff_login(payload: dict):
    email = str((payload or {}).get("email") or "").strip()
    password = str((payload or {}).get("password") or "")

    if not email or not password:
        return _response("Email and password are required", 400)

    user = _find_user_by_email(email)
    if not user:
        return _response("Invalid email or password", 401)
    if user.get("role") != STAFF_ROLE:
        return _response("This login is for staff accounts only", 403)
    if user.get("status") == "Blocked" or user.get("staff_active") is False:
        return _response("This staff account has been disabled", 403)

    try:
        valid = bool(VerifyHashed(user.get("password", ""), password))
    except Exception:
        valid = False

    if not valid:
        return _response("Invalid email or password", 401)

    token = create_access_token({
        "id": str(user["_id"]),
        "role": STAFF_ROLE,
        "scope": STAFF_ROLE,
    })

    return _response(
        "Login successful",
        200,
        token=token,
        user=_public_user(user),
        permissions=list(user.get("permissions") or []),
        must_change_password=bool(user.get("must_change_password")),
    )


@router.get("/me")
async def staff_me(user=Depends(staff_access(allow_password_change=True))):
    return _response("Staff profile fetched", 200, user=_public_user(user))


@router.post("/change-password")
async def staff_change_password(payload: dict, user=Depends(staff_access(allow_password_change=True))):
    current_password = str((payload or {}).get("current_password") or "")
    new_password = str((payload or {}).get("new_password") or "")

    if len(new_password) < 8:
        return _response("New password must be at least 8 characters", 400)
    if not current_password:
        return _response("Current password is required", 400)

    try:
        valid = bool(VerifyHashed(user.get("password", ""), current_password))
    except Exception:
        valid = False

    if not valid:
        return _response("Current password is incorrect", 400)

    user_collection.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "password": hashedpassword(new_password),
                "must_change_password": False,
                "password_updated_at": datetime.datetime.utcnow().isoformat(),
            }
        },
    )
    return _response("Password updated successfully", 200)


# ---------------------------------------------------------------------------
# Estates
# ---------------------------------------------------------------------------

@router.get("/houses")
async def staff_list_houses(user=Depends(staff_access(PERM_ESTATES))):
    try:
        data = []
        for house in list(house_collection.find({})):
            house["_id"] = str(house["_id"])
            new_images = []
            for img in house.get("house_image", []):
                if isinstance(img, list):
                    new_images.append(reassemble_base64_string(img))
                else:
                    new_images.append(get_image_url(img))
            house["house_image"] = new_images
            data.append(house)
        return _response("House data", 200, data=data)
    except Exception as exc:
        logger.error(f"Staff list houses failed: {exc}")
        return _response(str(exc), 500)


@router.get("/houses/{house_id}")
async def staff_get_house(house_id: str, user=Depends(staff_access(PERM_ESTATES))):
    house = house_collection.find_one({"_id": house_id})
    if not house:
        return _response("House not found", 404)

    images = []
    for img in house.get("house_image", []):
        if isinstance(img, list):
            images.append(reassemble_base64_string(img))
        else:
            images.append(get_image_url(img))
    house["house_image"] = images
    house["_id"] = str(house["_id"])
    return _response("House data", 200, data=house)


@router.post("/houses")
async def staff_add_house(
    house_name: str = Form(...),
    house_about: str = Form(...),
    house_location: str = Form(...),
    house_status: str = Form(...),
    house_pricing_plan: str = Form(...),
    house_landmarks: str = Form("[]"),
    house_benefits: str = Form("[]"),
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
    images: List[UploadFile] = File(None),
    user=Depends(staff_access(PERM_ESTATES)),
):
    try:
        image_urls = []
        for img in images or []:
            url, error = save_house_image(img)
            if error:
                return _response(error, 400)
            image_urls.append(url)

        now = datetime.datetime.utcnow().isoformat()
        data = {
            "house_name": house_name,
            "house_about": house_about,
            "house_location": house_location,
            "house_status": house_status,
            "house_pricing_plan": normalize_pricing_plans(json.loads(house_pricing_plan)),
            "house_landmarks": json.loads(house_landmarks),
            "house_benefits": json.loads(house_benefits),
            "house_type": house_type,
            "house_is_promo": house_is_promo,
            "house_promo_type": house_promo_type,
            "house_promo_value": house_promo_value,
            "land_enabled": land_enabled,
            "housing_enabled": housing_enabled,
            "property_type_prices": safe_json(property_type_prices, {}),
            "housing_unit_types": safe_json(housing_unit_types, []),
            "housing_registration_fee": optional_float(housing_registration_fee),
            "housing_savings_multiplier": optional_float(housing_savings_multiplier),
            "promo_visible": promo_visible,
            "promo_starts_at": promo_starts_at or "",
            "promo_ends_at": promo_ends_at or "",
            "promo_label": promo_label or "",
            "promo_terms": promo_terms or "",
            "promo_applies_to": promo_applies_to or "unit_price",
            "house_image": image_urls,
            "created_by": str(user["_id"]),
            "created_by_email": user.get("email", ""),
            "created_at": now,
            "updated_by": str(user["_id"]),
            "updated_by_email": user.get("email", ""),
            "updated_at": now,
        }

        house_collection.insert_one(data)
        notify_new_estate(house_name, house_location, image_urls, logger=logger)
        return _response("Estate created successfully", 200)
    except Exception as exc:
        logger.error(f"Staff add house failed: {exc}")
        return _response(f"Error adding estate: {exc}", 500)


@router.put("/houses/{house_id}")
async def staff_update_house(
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
    existing_images: str = Form("[]"),
    images: List[UploadFile] = File(None),
    user=Depends(staff_access(PERM_ESTATES)),
):
    try:
        existing_house = house_collection.find_one({"_id": house_id})
        if not existing_house:
            return _response("House not found", 404)

        update_data = {}
        if house_name: update_data["house_name"] = house_name
        if house_about: update_data["house_about"] = house_about
        if house_location: update_data["house_location"] = house_location
        if house_status: update_data["house_status"] = house_status
        if house_pricing_plan: update_data["house_pricing_plan"] = normalize_pricing_plans(json.loads(house_pricing_plan))
        if house_landmarks: update_data["house_landmarks"] = json.loads(house_landmarks)
        if house_benefits: update_data["house_benefits"] = json.loads(house_benefits)
        if house_type is not None: update_data["house_type"] = house_type
        if house_is_promo is not None: update_data["house_is_promo"] = house_is_promo
        if house_promo_type is not None: update_data["house_promo_type"] = house_promo_type
        if house_promo_value is not None: update_data["house_promo_value"] = house_promo_value
        if land_enabled is not None: update_data["land_enabled"] = land_enabled
        if housing_enabled is not None: update_data["housing_enabled"] = housing_enabled
        if property_type_prices is not None: update_data["property_type_prices"] = safe_json(property_type_prices, {})
        if housing_unit_types is not None: update_data["housing_unit_types"] = safe_json(housing_unit_types, [])
        if housing_registration_fee is not None: update_data["housing_registration_fee"] = optional_float(housing_registration_fee)
        if housing_savings_multiplier is not None: update_data["housing_savings_multiplier"] = optional_float(housing_savings_multiplier)
        if promo_visible is not None: update_data["promo_visible"] = promo_visible
        if promo_starts_at is not None: update_data["promo_starts_at"] = promo_starts_at
        if promo_ends_at is not None: update_data["promo_ends_at"] = promo_ends_at
        if promo_label is not None: update_data["promo_label"] = promo_label
        if promo_terms is not None: update_data["promo_terms"] = promo_terms
        if promo_applies_to is not None: update_data["promo_applies_to"] = promo_applies_to

        raw_existing = existing_images if existing_images not in (None, "") else "[]"
        try:
            parsed_existing = json.loads(raw_existing)
        except (TypeError, ValueError, json.JSONDecodeError):
            return _response("Invalid existing_images payload", 400)
        if not isinstance(parsed_existing, list):
            return _response("Invalid existing_images payload", 400)

        final_images = []
        for img in parsed_existing:
            if not isinstance(img, str):
                continue
            if "/uploads/" in img:
                rel_path = img.split("/uploads/")[1]
                final_images.append(f"/uploads/{rel_path}")
            else:
                final_images.append(img)

        for old_img in existing_house.get("house_image", []):
            if old_img not in final_images and isinstance(old_img, str) and "cloudinary" in old_img:
                delete_from_cloudinary(old_img)

        for img in images or []:
            url, error = save_house_image(img)
            if error:
                return _response(error, 400)
            final_images.append(url)

        if final_images or images:
            update_data["house_image"] = final_images

        update_data["updated_by"] = str(user["_id"])
        update_data["updated_by_email"] = user.get("email", "")
        update_data["updated_at"] = datetime.datetime.utcnow().isoformat()

        house_collection.update_one({"_id": house_id}, {"$set": update_data})
        return _response("Estate updated successfully", 200)
    except Exception as exc:
        logger.error(f"Staff update house failed: {exc}")
        return _response(f"Error updating estate: {exc}", 500)


@router.patch("/houses/{house_id}/status")
async def staff_update_house_status(house_id: str, payload: dict, user=Depends(staff_access(PERM_ESTATES))):
    new_status = str((payload or {}).get("house_status") or "").strip()
    if new_status not in {"Active", "Sold", "Inactive", "Draft"}:
        return _response("Invalid status", 400)

    existing = house_collection.find_one({"_id": house_id}, projection={"_id": 1})
    if not existing:
        return _response("House not found", 404)

    house_collection.update_one(
        {"_id": house_id},
        {
            "$set": {
                "house_status": new_status,
                "updated_by": str(user["_id"]),
                "updated_by_email": user.get("email", ""),
                "updated_at": datetime.datetime.utcnow().isoformat(),
            }
        },
    )
    return _response("Estate status updated", 200)


# ---------------------------------------------------------------------------
# JardProc products
# ---------------------------------------------------------------------------

@router.get("/products")
async def staff_list_products(category: str = "All", user=Depends(staff_access(PERM_PRODUCTS))):
    try:
        query = {} if category == "All" else {"category": category}
        products = list(products_collection.find(query))
        for product in products:
            product["_id"] = str(product["_id"])
            if isinstance(product.get("image"), list):
                product["image"] = [get_image_url(img) for img in product["image"]]
            else:
                product["image"] = get_image_url(product.get("image"))
        return _response("Products fetched", 200, data=products)
    except Exception as exc:
        logger.error(f"Staff list products failed: {exc}")
        return _response(str(exc), 500)


@router.post("/products")
async def staff_add_product(
    name: str = Form(...),
    description: str = Form(...),
    price: float = Form(...),
    category: str = Form(...),
    stock: int = Form(...),
    images: List[UploadFile] = File(None),
    volume_value: str = Form(None),
    volume_unit: str = Form(None),
    variants: str = Form(None),
    user=Depends(staff_access(PERM_PRODUCTS)),
):
    try:
        image_urls = []
        for img in images or []:
            image_urls.append(save_product_image(img))

        volume_val = None
        if volume_value and volume_value.strip():
            try:
                volume_val = float(volume_value)
            except ValueError:
                pass

        now = datetime.datetime.utcnow().isoformat()
        data = {
            "name": name,
            "description": description,
            "price": price,
            "category": category,
            "stock": stock,
            "image": image_urls,
            "status": "In Stock" if stock > 0 else "Out of Stock",
            "volume_value": volume_val,
            "volume_unit": volume_unit if volume_unit else None,
            "variants": json.loads(variants) if variants and variants.strip() else [],
            "created_at": now,
            "created_by": str(user["_id"]),
            "created_by_email": user.get("email", ""),
            "updated_by": str(user["_id"]),
            "updated_by_email": user.get("email", ""),
            "updated_at": now,
        }
        products_collection.insert_one(data)
        notify_new_product(name, image_urls, logger=logger)
        return _response("Product created successfully", 200)
    except Exception as exc:
        logger.error(f"Staff add product failed: {exc}")
        return _response(f"Error adding product: {exc}", 500)


@router.put("/products/{product_id}")
async def staff_update_product(
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
    variants: str = Form(None),
    user=Depends(staff_access(PERM_PRODUCTS)),
):
    try:
        product = products_collection.find_one({"_id": product_id})
        if not product:
            return _response("Product not found", 404)

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

        final_images = []
        if existing_images:
            final_images = json.loads(existing_images)

        for img in images or []:
            final_images.append(save_product_image(img))

        if final_images:
            update_data["image"] = final_images

        update_data["updated_by"] = str(user["_id"])
        update_data["updated_by_email"] = user.get("email", "")
        update_data["updated_at"] = datetime.datetime.utcnow().isoformat()

        products_collection.update_one({"_id": product_id}, {"$set": update_data})

        first_image = ""
        if final_images:
            first_image = final_images[0]
        elif product.get("image"):
            first_image = product["image"][0] if isinstance(product["image"], list) else product["image"]

        notify_updated_product(prod_name, first_image, logger=logger)
        return _response("Product updated successfully", 200)
    except Exception as exc:
        logger.error(f"Staff update product failed: {exc}")
        return _response(f"Error updating product: {exc}", 500)


# ---------------------------------------------------------------------------
# Support settings
# ---------------------------------------------------------------------------

@router.get("/settings/support")
async def staff_get_support_settings(user=Depends(staff_access(PERM_SUPPORT))):
    try:
        settings = sub_settings_collection.find_one({}) or {}
        return _response(
            "Support settings fetched",
            200,
            data={
                "support_whatsapp": str(settings.get("support_whatsapp") or SUPPORT_WHATSAPP_DEFAULT),
            },
        )
    except Exception as exc:
        logger.error(f"Staff get support settings failed: {exc}")
        return _response(str(exc), 500)


@router.put("/settings/support")
async def staff_update_support_settings(payload: dict, user=Depends(staff_access(PERM_SUPPORT))):
    try:
        raw = str((payload or {}).get("support_whatsapp") or "").strip()
        digits = "".join(ch for ch in raw if ch.isdigit())
        if not raw or not digits:
            return _response("Support WhatsApp number is required", 400)

        existing = sub_settings_collection.find_one({}) or {}
        existing.pop("_id", None)
        existing["support_whatsapp"] = digits
        existing["updated_at"] = datetime.datetime.utcnow().isoformat()
        existing["updated_by"] = str(user["_id"])
        existing["updated_by_email"] = user.get("email", "")
        sub_settings_collection.replace_one({}, existing, upsert=True)

        return _response(
            "Support settings updated",
            200,
            data={"support_whatsapp": digits},
        )
    except Exception as exc:
        logger.error(f"Staff update support settings failed: {exc}")
        return _response(str(exc), 500)
