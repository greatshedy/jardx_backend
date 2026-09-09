"""
One-off script: switch subscriptions to yearly.
1. Set sub_duration_months = 12 in subscription_settings.
2. Bump existing vendor/partner subscription records to 1 year:
   new_expiry = subscription_start + 1 year; if that is already in the
   past, use now + 1 year instead.
Run from the Backend directory:  python fix_yearly_subscription.py
"""
import datetime
from dateutil.relativedelta import relativedelta

from db.database import (
    vendors_collection,
    partners_collection,
    sub_settings_collection,
)

ONE_YEAR = relativedelta(months=12)
now = datetime.datetime.utcnow()

# 1. Update the subscription duration setting
sub_settings_collection.update_one(
    {},
    {"$set": {"sub_duration_months": 12}},
    upsert=True,
)
print("subscription_settings.sub_duration_months -> 12")

# 2. Bump existing vendor records
for item in list(vendors_collection.find({})):
    expiry = item.get("subscription_expiry")
    try:
        expiry_dt = datetime.datetime.fromisoformat(expiry) if expiry else None
    except Exception:
        expiry_dt = None
    if expiry_dt and expiry_dt > now + ONE_YEAR - datetime.timedelta(days=1):
        continue  # already effectively yearly

    start = item.get("subscription_start")
    try:
        start_dt = datetime.datetime.fromisoformat(start) if start else None
    except Exception:
        start_dt = None
    new_expiry = (start_dt or now) + ONE_YEAR
    if new_expiry < now:
        new_expiry = now + ONE_YEAR
    vendors_collection.update_one(
        {"_id": item["_id"]},
        {"$set": {"subscription_expiry": new_expiry.isoformat()}},
    )
    print(f"vendor {item.get('user_id')}: {expiry} -> {new_expiry.isoformat()}")

# 3. Bump existing partner records
for item in list(partners_collection.find({})):
    expiry = item.get("subscription_expiry")
    try:
        expiry_dt = datetime.datetime.fromisoformat(expiry) if expiry else None
    except Exception:
        expiry_dt = None
    if expiry_dt and expiry_dt > now + ONE_YEAR - datetime.timedelta(days=1):
        continue  # already effectively yearly

    start = item.get("subscription_start")
    try:
        start_dt = datetime.datetime.fromisoformat(start) if start else None
    except Exception:
        start_dt = None
    new_expiry = (start_dt or now) + ONE_YEAR
    if new_expiry < now:
        new_expiry = now + ONE_YEAR
    partners_collection.update_one(
        {"_id": item["_id"]},
        {"$set": {"subscription_expiry": new_expiry.isoformat()}},
    )
    print(f"partner {item.get('user_id')}: {expiry} -> {new_expiry.isoformat()}")

print("Done.")
