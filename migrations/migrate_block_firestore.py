"""One-time, idempotent migration of legacy Jard Block data from Firestore.

Usage:
    python migrations/migrate_block_firestore.py --credentials /path/to/service-account.json --dry-run
    python migrations/migrate_block_firestore.py --credentials /path/to/service-account.json --apply

Never commit the service-account file or its contents.
"""
import argparse
import datetime
import json
import os
import sys
import urllib.parse
from collections import Counter

import requests
from dateutil.relativedelta import relativedelta
from google.auth.transport.requests import Request
from google.oauth2 import service_account

# Ensure the backend package root is importable when run as a script.
BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from db.database import (  # noqa: E402
    block_investments_collection,
    block_plans_collection,
    block_referrals_collection,
    transactions_collection,
    user_collection,
)
from utill import upload_to_cloudinary  # noqa: E402

SCOPES = [
    "https://www.googleapis.com/auth/datastore",
    "https://www.googleapis.com/auth/cloud-platform",
]
PROJECT = "jardx-e2556"
FIRESTORE = f"https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/(default)/documents"


def decode_value(value):
    if not isinstance(value, dict):
        return value
    if "stringValue" in value:
        return value["stringValue"]
    if "integerValue" in value:
        return int(value["integerValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "booleanValue" in value:
        return value["booleanValue"]
    if "nullValue" in value:
        return None
    if "timestampValue" in value:
        return value["timestampValue"]
    if "mapValue" in value:
        return {k: decode_value(v) for k, v in value["mapValue"].get("fields", {}).items()}
    if "arrayValue" in value:
        return [decode_value(v) for v in value["arrayValue"].get("values", [])]
    return value


class FirestoreReader:
    def __init__(self, credentials_path):
        self.credentials = service_account.Credentials.from_service_account_file(
            credentials_path, scopes=SCOPES
        )
        self.credentials.refresh(Request())
        self.headers = {"Authorization": f"Bearer {self.credentials.token}"}
        self._buckets = None

    def documents(self, collection_id, page_size=100):
        output = []
        token = None
        while True:
            params = {"pageSize": page_size}
            if token:
                params["pageToken"] = token
            response = requests.get(
                f"{FIRESTORE}/{collection_id}", headers=self.headers, params=params, timeout=40
            )
            response.raise_for_status()
            payload = response.json()
            output.extend(
                {
                    "_legacy_id": doc.get("name", "").split("/")[-1],
                    **{k: decode_value(v) for k, v in doc.get("fields", {}).items()},
                }
                for doc in payload.get("documents", [])
            )
            token = payload.get("nextPageToken")
            if not token:
                break
        return output

    def list_buckets(self):
        if self._buckets is not None:
            return self._buckets
        response = requests.get(
            "https://storage.googleapis.com/storage/v1/b",
            headers=self.headers,
            params={"project": PROJECT},
            timeout=30,
        )
        if response.status_code != 200:
            self._buckets = []
            return self._buckets
        self._buckets = [item.get("name") for item in response.json().get("items", [])]
        return self._buckets

    def download_object(self, object_name):
        if not object_name:
            return None
        encoded = urllib.parse.quote(object_name, safe="")
        for bucket in self.list_buckets():
            try:
                response = requests.get(
                    f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/{encoded}",
                    headers=self.headers,
                    params={"alt": "media"},
                    timeout=60,
                )
                if response.status_code == 200 and response.content:
                    return response.content
            except Exception:
                continue
        return None


def parse_date(value):
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(
            tzinfo=None
        )
    except Exception:
        return None


def plan_map():
    mapping = {}
    for plan in block_plans_collection.find({}):
        mapping[str(plan.get("slug", "")).lower()] = plan
        mapping[str(plan.get("_id", ""))] = plan
    return mapping


def user_maps():
    by_email = {}
    by_legacy = {}
    for user in user_collection.find({}):
        email = str(user.get("email", "")).strip().lower()
        if email:
            by_email[email] = user
        legacy_uid = user.get("legacy_firebase_uid")
        if legacy_uid:
            by_legacy[str(legacy_uid)] = user
    return by_email, by_legacy


def append_user_id(user, field, value):
    if not user:
        return
    values = list(user.get(field) or [])
    if value in values:
        return
    values.append(value)
    user_collection.update_one({"_id": user.get("_id")}, {"$set": {field: values}})


def make_ledger(approval, moratorium_end, maturity, monthly_return, total_invested, principal, payouts_paid=0):
    entries = []
    for month in range(1, 13):
        entries.append(
            {
                "period": month,
                "kind": "roi",
                "due_at": (moratorium_end + relativedelta(months=month)).isoformat(),
                "amount": round(float(monthly_return or 0), 2),
                "status": "paid" if month <= int(payouts_paid or 0) else "pending",
                "paid_at": None,
                "paid_by": "legacy-migration" if month <= int(payouts_paid or 0) else None,
                "note": "Marked paid from legacy payoutsPaid snapshot" if month <= int(payouts_paid or 0) else "",
                "wallet_tx_ref": None,
            }
        )
    if principal:
        entries.append(
            {
                "period": "principal",
                "kind": "principal",
                "due_at": maturity.isoformat(),
                "amount": round(float(total_invested or 0), 2),
                "status": "pending",
                "paid_at": None,
                "paid_by": None,
                "note": "",
                "wallet_tx_ref": None,
            }
        )
    return entries


def status_for(now, moratorium_end, maturity):
    if now >= maturity:
        return "matured"
    if now >= moratorium_end:
        return "active"
    return "moratorium"


def build_investment(source, plan, user, legacy_id, status=None, proof_url=None, legacy_proof_path=None):
    now = datetime.datetime.utcnow()
    unit_price = float(source.get("unitPrice") or plan.get("unit_price") or 0)
    units = int(source.get("totalUnits") or 0)
    slots = int(source.get("slots") or 0)
    total_invested = float(source.get("totalInvested") or (unit_price * units) or 0)
    annual_roi = float(source.get("annualROI") or plan.get("annual_roi") or 0)
    annual_return = float(source.get("annualReturn") or (total_invested * annual_roi / 100) or 0)
    monthly_return = float(source.get("monthlyReturn") or (annual_return / 12) or 0)
    moratorium_months = int(source.get("moratoriumMonths") or plan.get("moratorium_months") or 4)
    payout_months = int(plan.get("payout_months") or 12)
    principal_return = bool(plan.get("principal_return", True))

    activated = parse_date(source.get("activatedAt") or source.get("createdAt")) or now
    moratorium_end = parse_date(source.get("moratoriumEndsAt")) or (
        activated + relativedelta(months=moratorium_months)
    )
    maturity = parse_date(source.get("payoutEndsAt")) or (
        moratorium_end + relativedelta(months=payout_months)
    )

    mapped_status = status or source.get("status") or "pending_review"
    if mapped_status in ("active", "moratorium", "approved"):
        mapped_status = status_for(now, moratorium_end, maturity)

    referral = {
        "code": "",
        "referrer_id": source.get("blockReferrerId") or source.get("referrerId"),
        "referrer_email": "",
        "rate": float(source.get("blockReferralRate") or 0),
        "commission_amount": round(
            total_invested * float(source.get("blockReferralRate") or 0), 2
        ),
        "status": "pending" if source.get("isBlockReferral") and source.get("blockReferralRate") else "none",
    }

    return {
        "ref": source.get("txRef") or f"BLOCK-LEGACY-{legacy_id[:10]}",
        "tx_ref": source.get("txRef") or f"BLOCK-LEGACY-{legacy_id[:10]}",
        "client_ref": f"legacy:{legacy_id}",
        "user_id": user.get("_id") if user else source.get("userId"),
        "user_email": user.get("email", "") if user else source.get("userEmail", ""),
        "user_name": source.get("userName", ""),
        "phone": user.get("phone_number", "") if user else "",
        "plan_id": str(plan.get("_id", "")),
        "plan_slug": plan.get("slug", source.get("planId", "")),
        "plan_name": plan.get("name", source.get("planName", "")),
        "plan_snapshot": {
            "slug": plan.get("slug", source.get("planId", "")),
            "name": plan.get("name", source.get("planName", "")),
            "unit_price": plan.get("unit_price", unit_price),
            "units_per_slot": plan.get("units_per_slot", source.get("unitsPerSlot", 1)),
            "annual_roi": plan.get("annual_roi", annual_roi),
            "moratorium_months": moratorium_months,
            "payout_months": payout_months,
            "principal_return": principal_return,
            "referral_rate": plan.get("referral_rate", 0),
        },
        "slots": slots,
        "units_per_slot": int(source.get("unitsPerSlot") or plan.get("units_per_slot") or 1),
        "total_units": units,
        "unit_price": unit_price,
        "total_invested": total_invested,
        "annual_roi": annual_roi,
        "annual_return": annual_return,
        "monthly_return": monthly_return,
        "moratorium_months": moratorium_months,
        "payout_months": payout_months,
        "principal_return": principal_return,
        "status": mapped_status,
        "payment_method": source.get("paymentMethod", "bank_transfer"),
        "transfer_reference": "",
        "proof_url": proof_url or "",
        "legacy_proof_path": legacy_proof_path or source.get("proofOfPayment") or "",
        "payouts_paid": int(source.get("payoutsPaid") or 0),
        "total_paid_out": float(source.get("totalPaidOut") or 0),
        "principal_paid": False,
        "payout_ledger": make_ledger(
            activated,
            moratorium_end,
            maturity,
            monthly_return,
            total_invested,
            principal_return,
            source.get("payoutsPaid") or 0,
        ),
        "referral": referral,
        "submitted_at": (parse_date(source.get("createdAt")) or now).isoformat(),
        "created_at": (parse_date(source.get("createdAt")) or now).isoformat(),
        "updated_at": now.isoformat(),
        "approval_date": activated.isoformat(),
        "moratorium_ends_at": moratorium_end.isoformat(),
        "first_payout_at": (moratorium_end + relativedelta(months=1)).isoformat(),
        "maturity_date": maturity.isoformat(),
        "reviewed_at": (parse_date(source.get("verifiedAt")) or activated).isoformat(),
        "reviewed_by": source.get("verifiedBy", "legacy-migration"),
        "admin_note": "",
        "legacy": True,
        "legacy_source": "firestore",
        "legacy_id": legacy_id,
        "legacy_uid": source.get("userId", ""),
    }


def load_realtime_proofs(path):
    """Map legacy txRef -> base64 data URI from the Realtime DB export."""
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        print(f"Realtime DB proof map unavailable: {exc}")
        return {}

    mapping = {}
    roots = [data.get("payment-proofs") or {}, data.get("block-payment-proofs") or {}]
    for root in roots:
        if not isinstance(root, dict):
            continue
        for _uid, items in root.items():
            if not isinstance(items, dict):
                continue
            for key, value in items.items():
                if not isinstance(value, dict):
                    continue
                tx_ref = value.get("txRef") or (key.split("_", 2)[0] + "_" + key.split("_", 2)[1] + "_" + key.split("_", 2)[2] if key.startswith("BLOCK_") and key.count("_") >= 2 else None)
                payload = value.get("base64Data") or value.get("fileData")
                if tx_ref and payload:
                    if not str(payload).startswith("data:"):
                        payload = f"data:image/jpeg;base64,{payload}"
                    mapping[str(tx_ref)] = payload
    return mapping


def copy_proof(reader, object_name, dry_run, realtime_proofs=None, tx_ref=None):
    if dry_run:
        return None
    content = reader.download_object(object_name) if object_name else None
    if not content and tx_ref and realtime_proofs:
        payload = realtime_proofs.get(str(tx_ref))
        if payload:
            return upload_to_cloudinary(payload, folder="block/legacy-proofs") or None
    if not content:
        return None
    return upload_to_cloudinary(content, folder="block/legacy-proofs") or None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--credentials", required=True, help="Firebase service-account JSON path")
    parser.add_argument("--apply", action="store_true", help="Write changes. Default is dry-run.")
    parser.add_argument("--dry-run", action="store_true", help="Explicitly run without writing (default).")
    parser.add_argument("--skip-proofs", action="store_true")
    parser.add_argument("--realtime-db", default="", help="Optional Realtime DB export JSON for proof fallback")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    dry_run = not args.apply
    reader = FirestoreReader(args.credentials)
    realtime_proofs = load_realtime_proofs(args.realtime_db) if args.realtime_db else {}
    if realtime_proofs:
        print(f"Realtime DB proof fallback entries: {len(realtime_proofs)}")
    plans = plan_map()
    by_email, by_legacy = user_maps()

    existing_investments = list(block_investments_collection.find({}))
    existing_by_legacy = {}
    existing_by_tx = {}
    for item in existing_investments:
        if item.get("legacy_id"):
            existing_by_legacy[str(item["legacy_id"])] = item
        if item.get("tx_ref"):
            existing_by_tx[str(item["tx_ref"])] = item

    all_users = list(user_collection.find({}))
    stats = Counter()
    unmatched_users = set()
    missing_proofs = 0

    def resolve_user(source):
        uid = source.get("userId")
        email = str(source.get("userEmail", "")).strip().lower()
        user = by_legacy.get(str(uid)) if uid else None
        if not user and email:
            user = by_email.get(email)
        if not user:
            # Fallback: scan once for a case-insensitive email match.
            for candidate in all_users:
                if str(candidate.get("email", "")).strip().lower() == email:
                    user = candidate
                    break
        if not user:
            unmatched_users.add(email or str(uid or "unknown"))
        return user

    active_records = reader.documents("blockInvestments")
    pending_records = reader.documents("pendingBlockInvestments")
    if args.limit:
        active_records = active_records[: args.limit]
        pending_records = pending_records[: args.limit]

    print(f"Firestore blockInvestments: {len(active_records)}")
    print(f"Firestore pendingBlockInvestments: {len(pending_records)}")

    # 1) Active holdings.
    for source in active_records:
        legacy_id = source["_legacy_id"]
        tx_ref = source.get("txRef")
        if legacy_id in existing_by_legacy or (tx_ref and tx_ref in existing_by_tx):
            stats["active_skipped_existing"] += 1
            continue
        plan_slug = str(source.get("planId", "")).lower()
        plan = plans.get(plan_slug) or plans.get(str(source.get("planName", "")).lower())
        if not plan:
            stats["active_skipped_unknown_plan"] += 1
            continue
        user = resolve_user(source)
        proof_url = copy_proof(reader, source.get("proofOfPayment"), dry_run or args.skip_proofs, realtime_proofs, source.get("txRef"))
        if source.get("proofOfPayment") and not proof_url and not dry_run and not args.skip_proofs:
            missing_proofs += 1
        doc = build_investment(source, plan, user, legacy_id, proof_url=proof_url)
        if dry_run:
            stats["active_would_import"] += 1
            existing_by_legacy[legacy_id] = {**doc, "_id": f"dry-{legacy_id}"}
        else:
            inserted = block_investments_collection.insert_one(doc)
            append_user_id(user, "block_investment_ids", str(inserted.inserted_id))
            existing_by_legacy[legacy_id] = {**doc, "_id": inserted.inserted_id}
            stats["active_imported"] += 1

    # 2) Pending / approved / rejected submissions.
    for source in pending_records:
        legacy_id = source["_legacy_id"]
        if legacy_id in existing_by_legacy:
            stats["pending_skipped_existing"] += 1
            continue
        confirmed_id = source.get("confirmedInvestmentId")
        if confirmed_id and str(confirmed_id) in existing_by_legacy:
            stats["pending_skipped_confirmed_active"] += 1
            if not dry_run:
                existing = existing_by_legacy[str(confirmed_id)]
                updates = {"legacy_confirmed_investment_id": str(confirmed_id)}
                if source.get("proofOfPayment"):
                    proof_url = copy_proof(reader, source.get("proofOfPayment"), args.skip_proofs, realtime_proofs, source.get("txRef"))
                    if proof_url:
                        updates["proof_url"] = proof_url
                        updates["legacy_proof_path"] = source.get("proofOfPayment")
                if source.get("txRef"):
                    updates["legacy_pending_tx_ref"] = source.get("txRef")
                try:
                    block_investments_collection.update_one({"_id": existing.get("_id")}, {"$set": updates})
                except Exception:
                    pass
            continue
        plan_slug = str(source.get("planId", "")).lower()
        plan = plans.get(plan_slug) or plans.get(str(source.get("planName", "")).lower())
        if not plan:
            stats["pending_skipped_unknown_plan"] += 1
            continue
        user = resolve_user(source)
        proof_url = copy_proof(reader, source.get("proofOfPayment"), dry_run or args.skip_proofs, realtime_proofs, source.get("txRef"))
        if source.get("proofOfPayment") and not proof_url and not dry_run and not args.skip_proofs:
            missing_proofs += 1
        if source.get("status") == "rejected":
            status = "rejected"
        elif source.get("status") == "approved":
            status = "approved"
        else:
            status = "pending_review"
        doc = build_investment(
            source,
            plan,
            user,
            legacy_id,
            status=status,
            proof_url=proof_url,
        )
        if source.get("confirmedInvestmentId"):
            doc["legacy_confirmed_investment_id"] = source.get("confirmedInvestmentId")
        if dry_run:
            stats["pending_would_import"] += 1
        else:
            inserted = block_investments_collection.insert_one(doc)
            append_user_id(user, "block_investment_ids", str(inserted.inserted_id))
            stats["pending_imported"] += 1

    # 3) Legacy block referrals.
    referral_records = reader.documents("blockReferralRegistrations")
    print(f"Firestore blockReferralRegistrations: {len(referral_records)}")
    existing_refs = {
        str(item.get("legacy_id")): item
        for item in block_referrals_collection.find({})
        if item.get("legacy_id")
    }
    for source in referral_records:
        legacy_id = source["_legacy_id"]
        if legacy_id in existing_refs:
            stats["referrals_skipped_existing"] += 1
            continue
        referrer = by_legacy.get(str(source.get("referrerId"))) or by_email.get(
            str(source.get("referrerEmail", "")).strip().lower()
        )
        referee = by_legacy.get(str(source.get("refereeId"))) or by_email.get(
            str(source.get("refereeEmail", "")).strip().lower()
        )
        if not referrer:
            unmatched_users.add(str(source.get("referrerEmail", "")))
        doc = {
            "referrer_id": referrer.get("_id") if referrer else source.get("referrerId"),
            "referrer_email": referrer.get("email", "") if referrer else "",
            "referee_id": referee.get("_id") if referee else source.get("refereeId"),
            "referee_email": source.get("refereeEmail", ""),
            "referee_name": source.get("refereeName", ""),
            "ref_code": source.get("refCode", ""),
            "plan_id": source.get("planId", ""),
            "plan_name": "",
            "rate": 0,
            "commission_amount": float(source.get("commissionAmount") or 0),
            "status": "legacy_registered",
            "created_at": parse_date(source.get("createdAt")).isoformat()
            if parse_date(source.get("createdAt"))
            else datetime.datetime.utcnow().isoformat(),
            "updated_at": datetime.datetime.utcnow().isoformat(),
            "legacy": True,
            "legacy_source": "firestore",
            "legacy_id": legacy_id,
        }
        if dry_run:
            stats["referrals_would_import"] += 1
        else:
            inserted = block_referrals_collection.insert_one(doc)
            append_user_id(referrer, "block_referral_ids", str(inserted.inserted_id))
            stats["referrals_imported"] += 1

    # 4) Legacy block transactions (dedupe by tx_ref).
    tx_records = reader.documents("transactions", page_size=200)
    block_txs = [
        item
        for item in tx_records
        if "block" in json.dumps(item, default=str).lower()
        and (str(item.get("txRef", "")).upper().startswith("BLOCK") or "block" in str(item.get("platform", "")).lower())
    ]
    print(f"Firestore block-related transactions: {len(block_txs)}")
    for source in block_txs:
        tx_ref = source.get("txRef")
        if not tx_ref:
            continue
        try:
            if transactions_collection.find_one({"tx_ref": tx_ref}):
                stats["transactions_skipped_existing"] += 1
                continue
        except Exception:
            pass
        status = "SUCCESS" if str(source.get("status", "")).lower() in ("completed", "success", "successful") else "PENDING"
        user = resolve_user(source)
        doc = {
            "tx_ref": tx_ref,
            "user_id": user.get("_id") if user else source.get("userId"),
            "amount": float(source.get("amount") or 0),
            "total_amount": float(source.get("totalPaid") or source.get("amount") or 0),
            "gateway": "Manual Bank Transfer",
            "type": "DEBIT",
            "purpose": source.get("title") or "Block Investment",
            "status": status,
            "source_type": "block_investment",
            "metadata": source.get("metadata") or {},
            "created_at": source.get("createdAt") or datetime.datetime.utcnow().isoformat(),
            "completed_at": source.get("completedAt") or "",
            "legacy": True,
            "legacy_source": "firestore",
        }
        if dry_run:
            stats["transactions_would_import"] += 1
        else:
            transactions_collection.insert_one(doc)
            stats["transactions_imported"] += 1

    print("\n--- MIGRATION SUMMARY ---")
    for key in sorted(stats):
        print(f"{key}: {stats[key]}")
    print(f"unmatched_users: {len(unmatched_users)}")
    print(f"missing_proofs: {missing_proofs}")
    print("mode:", "DRY RUN" if dry_run else "APPLIED")
    if dry_run:
        print("No changes were written. Re-run with --apply to import.")


if __name__ == "__main__":
    main()
