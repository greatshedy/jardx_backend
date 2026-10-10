"""
One-off script: backfill the `subscription_fee` on vendor/partner records.

Purpose
-------
Before fee-change notices existed, vendor/partner records did not store the
fee they were locked into. This script gives existing records a baseline so
that, when an admin later raises the fees, those users see the "fee increased"
notice (the new fee applies at their next renewal).

For each record missing `subscription_fee`, the locked fee is derived from the
most recent matching subscription transaction amount for that user; if none is
found it falls back to the historical default (Vendor 25,000 / Partner 100,000).

Run from the Backend directory:  python backfill_subscription_fee.py
"""
from db.database import (
    vendors_collection,
    partners_collection,
    transactions_collection,
)

VENDOR_DEFAULT_FEE = 25000.0
PARTNER_DEFAULT_FEE = 100000.0
VENDOR_PURPOSES = ("Vendor Registration Fee", "Vendor Subscription Renewal")
PARTNER_PURPOSES = ("Partner Registration Fee", "Partner Subscription Renewal")


def _latest_fee_from_transactions(user_id, purposes, default_fee):
    if user_id:
        try:
            txs = transactions_collection.find(
                {"user_id": user_id},
                projection={"amount": 1, "purpose": 1, "created_at": 1},
            ).sort({"created_at": -1})
            for tx in txs:
                if (tx.get("purpose") or "") in purposes and tx.get("amount") is not None:
                    try:
                        amount = float(tx["amount"])
                        if amount > 0:
                            return amount
                    except (TypeError, ValueError):
                        continue
        except Exception as e:
            print(f"  ! transaction lookup failed for {user_id}: {e}")
    return float(default_fee)


def backfill(collection, label, purposes, default_fee):
    updated = 0
    skipped = 0
    for item in list(collection.find({})):
        try:
            existing = item.get("subscription_fee")
            if existing is not None and float(existing) > 0:
                skipped += 1
                continue
        except (TypeError, ValueError):
            pass

        user_id = item.get("user_id")
        fee = _latest_fee_from_transactions(user_id, purposes, default_fee)
        collection.update_one(
            {"_id": item["_id"]},
            {"$set": {"subscription_fee": fee}},
        )
        updated += 1
        print(f"{label} {user_id}: subscription_fee -> {fee:,.0f}")

    print(f"{label}: {updated} updated, {skipped} already had a fee.")


if __name__ == "__main__":
    print("Backfilling subscription_fee ...")
    backfill(vendors_collection, "vendor", VENDOR_PURPOSES, VENDOR_DEFAULT_FEE)
    backfill(partners_collection, "partner", PARTNER_PURPOSES, PARTNER_DEFAULT_FEE)
    print("Done.")
