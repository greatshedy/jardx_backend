"""Web Push (VAPID) delivery for admin browser notifications.

Requires `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, and optionally `VAPID_SUBJECT`
in the environment, and the `pywebpush` package. Failures are non-fatal.
"""
import json
import logging
import os

logger = logging.getLogger("jardx")


def _vapid_configured() -> bool:
    return bool(os.getenv("VAPID_PRIVATE_KEY") and os.getenv("VAPID_PUBLIC_KEY"))


def send_admin_web_push(title: str, body: str, url: str = "/dashboard", tag: str = None) -> int:
    """Send a web-push notification to every stored admin subscription.

    Returns the number of notifications successfully dispatched.
    """
    private_key = os.getenv("VAPID_PRIVATE_KEY")
    if not private_key:
        logger.warning("webpush skipped: VAPID_PRIVATE_KEY not set")
        return 0

    try:
        from pywebpush import webpush, WebPushException
    except Exception as e:
        logger.error(f"pywebpush unavailable: {e}")
        return 0

    try:
        from db.database import admin_push_subscriptions_collection as subs
    except Exception as e:
        logger.error(f"webpush: subscriptions collection unavailable: {e}")
        return 0

    subject = os.getenv("VAPID_SUBJECT", "mailto:jarvadgroup.business@gmail.com")
    payload = json.dumps({
        "title": title,
        "body": body,
        "url": url,
        "tag": tag or url,
    })

    sent = 0
    try:
        subscriptions = list(subs.find({}))
    except Exception as e:
        logger.error(f"webpush: failed to load subscriptions: {e}")
        return 0

    for s in subscriptions:
        endpoint = s.get("endpoint")
        keys = s.get("keys") or {}
        if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
            continue
        info = {"endpoint": endpoint, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]}}
        try:
            webpush(
                subscription_info=info,
                data=payload,
                vapid_private_key=private_key,
                vapid_claims={"sub": subject},
            )
            sent += 1
        except WebPushException as ex:
            status = getattr(getattr(ex, "response", None), "status_code", None)
            if status in (404, 410):
                try:
                    subs.delete_one({"endpoint": endpoint})
                    logger.info("Pruned expired push subscription.")
                except Exception:
                    pass
            else:
                logger.error(f"webpush error ({status}): {ex}")
        except Exception as ex:
            logger.error(f"webpush unexpected error: {ex}")

    if sent:
        logger.info(f"Admin web push sent to {sent} subscription(s)")
    return sent
