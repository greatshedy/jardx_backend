import datetime

from fastapi import UploadFile

from utill import get_image_url, send_push_notification_to_user, upload_to_cloudinary


def save_product_image(file: UploadFile):
    contents = file.file.read()
    return upload_to_cloudinary(contents, folder="products")


def notify_new_product(name: str, image_urls: list, logger=None):
    try:
        from db.database import notifications_collection, user_collection

        first_image = get_image_url(image_urls[0]) if image_urls else ""
        all_users = list(user_collection.find({}))
        now = datetime.datetime.utcnow().isoformat()

        in_app_notifs = []
        for u in all_users:
            in_app_notifs.append({
                "user_id": str(u["_id"]),
                "title": "New Product Alert!",
                "body": f"We just added '{name}' to our store. Check it out now!",
                "type": "PRODUCT",
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
                    title="New Product Alert!",
                    body=f"We just added '{name}' to our store. Check it out now!",
                    data_payload={"screen": "notifications", "image": first_image},
                )
    except Exception as exc:
        if logger:
            logger.error(f"Failed to send add-product push notifications: {exc}")


def notify_updated_product(name: str, first_image: str, logger=None):
    try:
        from db.database import notifications_collection, user_collection

        resolved_image = get_image_url(first_image) if first_image else ""
        all_users = list(user_collection.find({}))
        now = datetime.datetime.utcnow().isoformat()

        in_app_notifs = []
        for u in all_users:
            in_app_notifs.append({
                "user_id": str(u["_id"]),
                "title": "Product Updated!",
                "body": f"The product '{name}' has been updated with new details.",
                "type": "PRODUCT",
                "action_text": "View",
                "created_at": now,
                "is_read": False,
                "image": resolved_image,
            })
        if in_app_notifs:
            notifications_collection.insert_many(in_app_notifs)

        for u in all_users:
            if u.get("push_token") and (u.get("notification_settings") or {}).get("push", False):
                send_push_notification_to_user(
                    user_doc=u,
                    title="Product Updated!",
                    body=f"The product '{name}' has been updated with new details.",
                    data_payload={"screen": "notifications", "image": resolved_image},
                )
    except Exception as exc:
        if logger:
            logger.error(f"Failed to send update-product push notifications: {exc}")
