import datetime
import json
import os

from fastapi import UploadFile

from utill import get_image_url, send_push_notification_to_user, upload_to_cloudinary


def safe_json(value, fallback):
    try:
        if value in (None, ""):
            return fallback
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def optional_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_pricing_plans(plans):
    """Normalize pricing plan numbers and derive per-plan discount metadata."""
    normalized = []
    if not isinstance(plans, list):
        return normalized
    for raw in plans:
        if not isinstance(raw, dict):
            continue
        plan = dict(raw)
        sale_price = optional_float(plan.get("outrightPrice"))
        if sale_price is None:
            sale_price = optional_float(plan.get("price"))
        sale_price = max(0.0, sale_price or 0.0)
        original_price = optional_float(plan.get("originalPrice"))
        if original_price is not None:
            original_price = max(0.0, original_price)
        if original_price is None or original_price <= sale_price:
            original_price = None
        plan["outrightPrice"] = sale_price
        plan["originalPrice"] = original_price
        if original_price and original_price > sale_price:
            plan["discountAmount"] = round(original_price - sale_price, 2)
            plan["discountPercent"] = int(round((1 - (sale_price / original_price)) * 100))
        else:
            plan["discountAmount"] = 0.0
            plan["discountPercent"] = 0
        for key in ("percentageIncrease", "downPayment"):
            value = optional_float(plan.get(key))
            plan[key] = value if value is not None else plan.get(key)
        normalized.append(plan)
    return normalized


def save_house_image(file: UploadFile):
    file.file.seek(0, os.SEEK_END)
    file_size = file.file.tell()
    file.file.seek(0)

    if file_size > 1024 * 1024:
        return None, "File size exceeds 1MB limit."

    contents = file.file.read()
    url = upload_to_cloudinary(contents, folder="houses")

    if not url:
        return None, "Upload to Cloudinary failed."

    return url, None


def notify_new_estate(house_name: str, house_location: str, image_urls: list, logger=None):
    """Best-effort in-app + push notification for a newly created estate."""
    try:
        from db.database import notifications_collection, user_collection

        first_image = get_image_url(image_urls[0]) if image_urls else ""
        all_users = list(user_collection.find({}))
        now = datetime.datetime.utcnow().isoformat()

        in_app_notifs = []
        for u in all_users:
            in_app_notifs.append({
                "user_id": str(u["_id"]),
                "title": "New Real Estate Listed!",
                "body": f"A premium property '{house_name}' is now available in {house_location}.",
                "type": "ESTATE",
                "action_text": "View",
                "created_at": now,
                "is_read": False,
                "image": first_image,
            })
        if in_app_notifs:
            notifications_collection.insert_many(in_app_notifs)

        for u in all_users:
            if u.get("push_token") and (u.get("notification_settings") or {}).get("push", False):
                send_push_notification_to_user(
                    user_doc=u,
                    title="New Real Estate Listed!",
                    body=f"A premium property '{house_name}' is now available in {house_location}.",
                    data_payload={"screen": "notifications", "image": first_image},
                )
    except Exception as exc:
        if logger:
            logger.error(f"Failed to send add-house push notifications: {exc}")
