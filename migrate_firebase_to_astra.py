"""Firebase (Realtime + Firestore) -> live Astra (.env.new) migration.

Safety: additive-only, skip-existing (by _id for all; by _id+email for users),
backup-first (run backup_live.py), dry-run by default.

Usage:
  python migrate_firebase_to_astra.py --phase 1 --dry-run
  python migrate_firebase_to_astra.py --phase 1 --yes
  python migrate_firebase_to_astra.py --phase 2 --yes
  python migrate_firebase_to_astra.py --phase 3 --yes
"""
import argparse
import datetime
import json
import os
import re
from pathlib import Path

from dotenv import dotenv_values
from astrapy import DataAPIClient
from astrapy.api_options import APIOptions, TimeoutOptions

BASE = Path(__file__).resolve().parent
ENV = BASE / ".env.new"
ENV_LOCAL = BASE / ".env"
FS_DIR = BASE / "firebase_dump" / "firestore"
RT_JSON = Path(r"C:\Users\NEW USER\Downloads\jarvard firebase realtime db.json")
REQUIRED = ["DB_URL", "DB_TOKEN", "DB_AUX_URL", "DB_AUX_TOKEN"]
NOW = datetime.datetime.utcnow().isoformat()

# ---------------------------------------------------------------- helpers

def load_env(path):
    vals = dotenv_values(str(path))
    missing = [k for k in REQUIRED if not vals.get(k)]
    if missing:
        raise SystemExit("ERROR: %s missing %s" % (path, ", ".join(missing)))
    return vals


def connect(vals, aux=False):
    p = "DB_AUX" if aux else "DB"
    client = DataAPIClient(
        vals[p + "_TOKEN"],
        api_options=APIOptions(timeout_options=TimeoutOptions(request_timeout_ms=90000)),
    )
    return client.get_database_by_api_endpoint(vals[p + "_URL"])


def load_fs(name):
    p = FS_DIR / (name + ".json")
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def norm_email(s):
    return (s or "").strip().lower()


def chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def intnum(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- phase 1: users

def build_users(fs_users, rt, fs_linked_banks, fs_user_emails, image_map, upload_images):
    rt_users = rt.get("users", {}) if isinstance(rt, dict) else {}
    merged = {}

    # Realtime baseline
    for uid, rec in rt_users.items():
        p = rec.get("profile", {}) if isinstance(rec, dict) else {}
        merged[uid] = {
            "_id": uid, "uid": uid,
            "user_name": p.get("fullName", "") or "",
            "email": norm_email(p.get("email")),
            "phone_number": p.get("phoneNumber", "") or "",
            "profile_pic": p.get("profileImageUrl", "") or "",
            "wallet_balance": 0.0,
            "earnings_balance": 0.0,
            "referral_code": "", "referred_by": "",
            "is_referral_active": False, "referral_percentage": 0.0,
            "referral_bonus_paid": False,
            "partner_type": "normal", "is_partner": False,
            "status": "Active", "created_at": "",
            "_score": 1,
        }

    # Firestore overlay (wins)
    for u in fs_users:
        uid = u.get("_id") or u.get("uid")
        if not uid:
            continue
        email = norm_email(u.get("email"))
        base = merged.get(uid) or {"_id": uid, "uid": uid, "_score": 0}
        score = base.get("_score", 0) + 10
        base.update({
            "_id": uid, "uid": uid,
            "user_name": u.get("displayName") or base.get("user_name", ""),
            "email": email or base.get("email", ""),
            "phone_number": u.get("phone") or base.get("phone_number", ""),
            "wallet_balance": num(u.get("walletBalance")),
            "earnings_balance": num(u.get("totalEarnings")),
            "referral_code": u.get("referralCode") or base.get("referral_code", ""),
            "referred_by": u.get("referrerId") or u.get("referredBy") or base.get("referred_by", ""),
            "is_referral_active": bool(u.get("canRefer")),
            "partner_type": u.get("userType") or "normal",
            "is_partner": (u.get("userType") == "partner"),
            "status": "Blocked" if norm_email(u.get("accountStatus")) in ("blocked", "suspended", "disabled") else "Active",
            "created_at": u.get("createdAt") or base.get("created_at", ""),
            "password": "", "otp": "", "transaction_pin": "",
            "migrated": True, "migrated_at": NOW,
            "_score": score,
        })
        merged[uid] = base

    # linkedBanks -> bank_accounts
    banks_by_uid = {}
    for b in fs_linked_banks:
        uid = b.get("userId")
        if not uid:
            continue
        banks_by_uid.setdefault(uid, []).append({
            "id": b.get("_id", ""),
            "bank_name": b.get("bankName") or b.get("name") or "",
            "account_number": b.get("accountNumber") or "",
            "account_name": b.get("accountHolderName") or b.get("accountName") or "",
            "account_type": b.get("type") or "",
            "bank_slug": b.get("bankCode") or "",
        })

    # dedupe by email (keep highest score)
    by_email = {}
    dropped = []
    final = []
    for u in merged.values():
        e = u.get("email")
        if not e:
            u.setdefault("bank_accounts", [])
            final.append(u)
            continue
        prev = by_email.get(e)
        if prev is None or u.get("_score", 0) > prev.get("_score", 0):
            if prev is not None:
                dropped.append(prev["_id"])
            by_email[e] = u
        else:
            dropped.append(u["_id"])

    result = []
    for u in merged.values():
        if u.get("email") and by_email.get(u["email"]) is not u:
            continue
        u.setdefault("bank_accounts", banks_by_uid.get(u["_id"], []))
        u.pop("_score", None)
        if not u.get("profile_pic"):
            u["profile_pic"] = image_map.get(u["_id"], "") or ""
        result.append(u)
    return result, dropped


# ---------------------------------------------------------------- cloudinary

_cloud_cfg = {"done": False, "ok": False}

def _init_cloudinary():
    if _cloud_cfg["done"]:
        return _cloud_cfg["ok"]
    _cloud_cfg["done"] = True
    try:
        import cloudinary  # noqa
        url = None
        if ENV_LOCAL.exists():
            url = dotenv_values(str(ENV_LOCAL)).get("CLOUDINARY_URL") or os.getenv("CLOUDINARY_URL")
        url = url or os.getenv("CLOUDINARY_URL")
        if not url:
            print("  (cloudinary: no CLOUDINARY_URL found; skipping image upload)")
            return False
        parts = url.replace("cloudinary://", "").replace('"', "").replace("'", "")
        creds, cloud_name = parts.split("@", 1)
        api_key, api_secret = creds.split(":", 1)
        cloudinary.config(cloud_name=cloud_name, api_key=api_key, api_secret=api_secret, secure=True)
        _cloud_cfg["ok"] = True
    except Exception as e:
        print("  (cloudinary init failed: %s)" % e)
    return _cloud_cfg["ok"]


def upload_image(data_url):
    if not _init_cloudinary():
        return None
    try:
        import cloudinary.uploader
        res = cloudinary.uploader.upload(data_url, folder="users/profiles")
        return res.get("secure_url")
    except Exception as e:
        print("  (image upload failed: %s)" % str(e)[:120])
        return None


# Astra rejects indexed strings > 8000 bytes; base64 images must become URLs.
MAX_STR = 7900
DO_UPLOADS = False
STATS = {"images_uploaded": 0, "images_dropped": 0, "strings_truncated": 0}


def sanitize(obj):
    if isinstance(obj, str):
        if obj.startswith("data:") or len(obj) > MAX_STR:
            if DO_UPLOADS and obj.startswith("data:image"):
                url = upload_image(obj)
                if url:
                    STATS["images_uploaded"] += 1
                    return url
            STATS["images_dropped"] += 1
            return ""
        return obj
    if isinstance(obj, list):
        return [sanitize(x) for x in obj]
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------- phase 2 mappers

def uid_of(doc, by_uid, by_email):
    uid = doc.get("userId")
    if uid:
        return uid
    email = norm_email(doc.get("userEmail") or doc.get("email") or doc.get("investorEmail"))
    if email and email in by_email:
        return by_email[email]
    return ""


def _iso(v):
    return v if isinstance(v, str) else ("" if v is None else str(v))


def map_estates(fs):
    out = []
    for e in fs.get("estates", []):
        pricing = []
        for o in (e.get("pricingOptions") or []):
            if not isinstance(o, dict):
                continue
            plan = (o.get("paymentPlan") or "").lower()
            months = re.findall(r"(\d+)\s*month", plan)
            pricing.append({
                "outrightPrice": num(o.get("price")) if "outright" in plan else None,
                "downPayment": 0,
                "percentageIncrease": 0,
                "numInstallments": [int(months[0])] if months else [],
                "unitSqm": intnum(re.findall(r"(\d+)", o.get("plotSize") or "0")[0]) if re.findall(r"(\d+)", o.get("plotSize") or "") else 0,
                "name": o.get("name") or "",
                "paymentPlan": o.get("paymentPlan") or "",
                "price": num(o.get("price")),
                "originalPrice": num(o.get("originalPrice")),
                "documentationIncluded": bool(o.get("documentationIncluded")),
            })
        out.append({
            "_id": e.get("_id"),
            "house_name": e.get("title") or "",
            "house_about": e.get("description") or e.get("shortDescription") or "",
            "house_location": e.get("location") or "",
            "house_status": "Active" if str(e.get("status", "")).lower() in ("", "active", "published") else _iso(e.get("status")),
            "house_type": e.get("titleType") or "",
            "house_landmarks": e.get("landmarks") or [],
            "house_benefits": e.get("benefits") or [],
            "house_image": [x for x in [e.get("heroImage"), e.get("bgImage")] if x],
            "house_is_promo": bool(e.get("isPromo")),
            "house_promo_type": "",
            "house_promo_value": 0.0,
            "house_pricing_plan": pricing,
            "source_pricing_options": e.get("pricingOptions") or [],
            "created_at": _iso(e.get("createdAt")),
            "migrated": True,
        })
    return out


def map_portfolio(fs, by_uid, by_email):
    out = []
    for p in fs.get("estatePurchases", []):
        total = num(p.get("landPrice"))
        paid = num(p.get("amount"))
        out.append({
            "_id": p.get("_id"),
            "user_id": uid_of(p, by_uid, by_email),
            "house_id": p.get("estateId") or "",
            "house_name": p.get("estateTitle") or "",
            "plan_type": "outright" if "outright" in (p.get("selectedPlan") or "").lower() else "installment",
            "total_price": total, "amount_paid": paid,
            "remaining_balance": max(0.0, total - paid),
            "monthly_payment": 0.0, "duration_months": 0, "months_paid": 0,
            "next_payment_date": "",
            "status": "Completed" if str(p.get("status", "")).lower() in ("completed", "paid") else "Active",
            "created_at": _iso(p.get("createdAt")),
            "source_collection": "estatePurchases", "migrated": True,
        })
    for pl in fs.get("installmentPlans", []):
        installments = pl.get("installments") or []
        total = num(pl.get("totalAmount"))
        paid = num(pl.get("totalPaid"))
        months = len(installments) or intnum(re.findall(r"(\d+)", pl.get("pricingOptionName") or "") and re.findall(r"(\d+)", pl.get("pricingOptionName"))[0])
        out.append({
            "_id": pl.get("_id"),
            "user_id": uid_of(pl, by_uid, by_email),
            "house_id": pl.get("propertyId") or "",
            "house_name": pl.get("propertyTitle") or "",
            "plan_type": "installment",
            "total_price": total, "amount_paid": paid,
            "remaining_balance": num(pl.get("remainingAmount")),
            "monthly_payment": round(total / months, 2) if months else 0.0,
            "duration_months": months,
            "months_paid": intnum(pl.get("paidInstallments")),
            "next_payment_date": _iso(pl.get("nextDueDate")),
            "status": "Completed" if str(pl.get("status", "")).lower() in ("completed", "paid") else "Active",
            "created_at": _iso(pl.get("createdAt")),
            "source_collection": "installmentPlans", "migrated": True,
        })
    return out


def map_kidvest(fs, by_uid, by_email):
    out = []
    for k in fs.get("kidvestInvestments", []):
        period = intnum(k.get("investmentPeriod"))
        out.append({
            "_id": k.get("_id"),
            "user_id": uid_of(k, by_uid, by_email),
            "child_name": k.get("childName") or k.get("kidName") or "",
            "child_dob": k.get("childDateOfBirth") or "",
            "child_gender": k.get("childGender") or "",
            "plan_type": "child_savings",
            "monthly_amount": num(k.get("fixedMonthlyAmount"), num(k.get("monthlyContribution"))),
            "total_months": intnum(k.get("totalMonthsInPlan")),
            "months_paid": intnum(k.get("monthsPaidSoFar")),
            "investment_period_years": int(round(period / 12)) if period else 0,
            "return_percentage": num(k.get("returnPercentage")),
            "expected_return": num(k.get("expectedMaturity")),
            "total_balance": num(k.get("totalPaidSoFar")),
            "status": "Completed" if str(k.get("status", "")).lower() in ("completed", "matured") else "Active",
            "next_payment_date": _iso(k.get("nextPaymentDue")),
            "created_at": _iso(k.get("createdAt")),
            "source_collection": "kidvestInvestments", "migrated": True,
        })
    return out


def map_partnerships(fs, level):
    out = []
    for p in fs.get("partnerships", []):
        if (p.get("partnershipLevel") or "").lower() != level:
            continue
        out.append({
            "_id": p.get("_id"),
            "user_id": p.get("userId") or "",
            "fullName": p.get("fullName") or "",
            "email": p.get("email") or p.get("userEmail") or "",
            "phone": p.get("phone") or "",
            "address": p.get("address") or "",
            "profession": p.get("fieldOfWork") or "",
            "skills": "", "vocation": "",
            "accountNumber": "", "bankName": "",
            "photo": "", "certificate": "", "gallery": [],
            "description": p.get("experience") or "",
            "workExperience": p.get("experience") or "",
            "accountType": level,
            "status": "verified" if str(p.get("status", "")).lower() == "approved" else "unverified",
            "subscription_start": _iso(p.get("lastPaymentDate") or p.get("applicationDate")),
            "subscription_expiry": _iso(p.get("expiresAt")),
            "subscription_status": "active",
            "subscription_fee": num(p.get("annualFee")),
            "auto_debit": bool(p.get("autoRenew")),
            "created_at": _iso(p.get("applicationDate")),
            "migrated": True,
        })
    return out


CREDIT_TYPES = {"funding", "first_deposit", "credit"}
DEBIT_TYPES = {"property_deposit", "property_purchase", "withdrawal", "installment_payment",
               "installment_down_payment", "partner_partnership_payment", "partnership_payment",
               "vendor_partnership_payment", "kidvest_investment", "estate_investment",
               "block_investment", "entrance_fee"}
GW = {"wallet": "Wallet", "monnify": "Monnify", "flutterwave": "Flutterwave",
      "bank_transfer": "Manual Bank Transfer", "commission_reinvestment": "Referral System"}
ST = {"completed": "SUCCESS", "pending": "PENDING", "active": "SUCCESS", "failed": "FAILED",
      "rejected": "DECLINED", "in_review": "PENDING", "pending_verification": "PENDING",
      "partial_payment": "PENDING"}


def classify(t):
    ty = (t.get("type") or "").lower()
    if ty in CREDIT_TYPES:
        return "CREDIT"
    if ty in DEBIT_TYPES:
        return "DEBIT"
    if ty == "deposit":
        title = (t.get("title") or "").lower()
        return "CREDIT" if t.get("platform") == "wallet" or "wallet funding" in title else "DEBIT"
    if ty == "system":
        return "CREDIT" if not num(t.get("amount")) else "DEBIT"
    return "DEBIT"


def _mk_tx(t, uid, tx_ref=None, amount=None, gateway=None, ttype=None, purpose=None, status=None):
    created = _iso(t.get("createdAt"))
    return {
        "_id": t.get("_id"),
        "tx_ref": tx_ref or t.get("txRef") or t.get("_id"),
        "user_id": uid,
        "amount": num(amount if amount is not None else t.get("amount")),
        "total_amount": t.get("totalPaid"),
        "gateway": gateway or GW.get((t.get("paymentMethod") or "").lower(), "Wallet"),
        "type": ttype or classify(t),
        "purpose": purpose or t.get("title") or t.get("description") or (t.get("type") or ""),
        "status": status or ST.get((t.get("status") or "").lower(), "SUCCESS"),
        "is_manual": (t.get("paymentMethod") == "bank_transfer"),
        "created_at": created,
        "completed_at": created if (t.get("status") == "completed") else "",
        "source_type": t.get("type"),
        "metadata": t.get("metadata") or {},
        "migrated": True,
    }


def map_transactions(fs, by_uid, by_email):
    out = []
    unresolved = 0
    for t in fs.get("transactions", []):
        uid = uid_of(t, by_uid, by_email)
        if not uid:
            unresolved += 1
        out.append(_mk_tx(t, uid))
    for b in fs.get("bankTransfers", []):
        uid = uid_of(b, by_uid, by_email)
        out.append(_mk_tx(b, uid, ttype="DEBIT", purpose=b.get("description") or b.get("propertyTitle") or "Bank Transfer"))
    for k in fs.get("kidvestPaymentHistory", []):
        uid = k.get("userId") or ""
        out.append(_mk_tx(k, uid, tx_ref=k.get("txRef") or k.get("transactionId"), amount=k.get("amount"),
                          gateway=GW.get((k.get("paymentMethod") or "").lower(), "Wallet"),
                          ttype="DEBIT", purpose="JardKidz Payment",
                          status=ST.get((k.get("status") or "").lower(), "SUCCESS")))
    for p in fs.get("partnershipPayments", []):
        uid = p.get("userId") or ""
        out.append(_mk_tx(p, uid, ttype="DEBIT", purpose="Partnership Payment"))
    return out, unresolved


def map_withdrawals(fs, by_uid, by_email):
    out = []
    for w in fs.get("withdrawalRequests", []):
        uid = w.get("userId") or uid_of(w, by_uid, by_email)
        out.append({
            "_id": w.get("_id"),
            "user_id": uid,
            "amount": num(w.get("amount")),
            "gross_amount": w.get("grossAmount"),
            "tax_fee": w.get("taxFee"),
            "status": (w.get("status") or "").lower(),
            "bank_details": w.get("bankDetails"),
            "tx_ref": w.get("txRef") or "",
            "reference": w.get("transferReference") or "",
            "created_at": _iso(w.get("requestedAt")),
            "migrated": True,
        })
    return out


def copy_raw(fs, name):
    out = []
    for d in fs.get(name, []):
        d = dict(d)
        d["migrated"] = True
        out.append(d)
    return out


# ---------------------------------------------------------------- build plan

def build(fs, rt, upload_images):
    fs_users = fs.get("users", [])
    rt_users = rt.get("users", {}) if isinstance(rt, dict) else {}
    fs_banks = fs.get("linkedBanks", [])
    image_map = rt.get("profile_images", {}) if isinstance(rt, dict) else {}
    users, dropped = build_users(fs_users, rt, fs_banks, fs.get("userEmails", []), image_map, upload_images)

    by_uid = {u["_id"]: u for u in users}
    by_email = {u["email"]: u["_id"] for u in users if u.get("email")}

    tx, unresolved = map_transactions(fs, by_uid, by_email)

    plan = {
        "users": ("main", users),
        "jard_houses": ("main", map_estates(fs)),
        "portfolio": ("main", map_portfolio(fs, by_uid, by_email)),
        "jard_kidz_plans": ("main", map_kidvest(fs, by_uid, by_email)),
        "vendors": ("main", map_partnerships(fs, "vendor")),
        "partners": ("main", map_partnerships(fs, "partner")),
        "payment_transactions": ("main", tx),
        "withdraw_requests": ("aux", map_withdrawals(fs, by_uid, by_email)),
        # phase 3 (new collections, raw copy) -> aux keyspace (main is at the 100-index limit)
        "referral_commissions": ("aux", copy_raw(fs, "referralCommissions")),
        "referral_activities": ("aux", copy_raw(fs, "referralActivities")),
        "kids_profiles": ("aux", copy_raw(fs, "kidsProfiles")),
        "promos": ("aux", copy_raw(fs, "promos")),
        "land_purchases": ("aux", copy_raw(fs, "landPurchases")),
        "land_payments": ("aux", copy_raw(fs, "landPayments")),
        "land_referral_relationships": ("aux", copy_raw(fs, "landReferralRelationships")),
        "support_messages": ("aux", copy_raw(fs, "support_messages")),
    }
    for name, (group, docs) in plan.items():
        plan[name] = (group, [sanitize(d) for d in docs])

    return plan, {"users_dropped_dupes": len(dropped), "tx_unresolved_user": unresolved,
                  "users_total": len(users), **STATS}


PHASE1 = ["users"]
PHASE2 = ["jard_houses", "portfolio", "jard_kidz_plans", "vendors", "partners",
          "payment_transactions", "withdraw_requests"]
PHASE3 = ["referral_commissions", "referral_activities", "kids_profiles", "promos",
          "land_purchases", "land_payments", "land_referral_relationships", "support_messages"]


def import_group(db, name, docs, is_users=False):
    available = set(db.list_collection_names())
    if name not in available:
        col = db.create_collection(name)
    else:
        col = db.get_collection(name)

    existing_ids = set()
    existing_emails = set()
    try:
        for d in col.find({}, projection={"_id": 1, "email": 1}):
            existing_ids.add(str(d.get("_id")))
            if is_users and d.get("email"):
                existing_emails.add(d["email"])
    except Exception:
        for d in col.find({}, projection={"_id": 1}):
            existing_ids.add(str(d.get("_id")))

    fresh = []
    skipped = 0
    for d in docs:
        if not d.get("_id"):
            continue
        if str(d["_id"]) in existing_ids:
            skipped += 1
            continue
        if is_users and d.get("email") and d["email"] in existing_emails:
            skipped += 1
            continue
        fresh.append(d)

    for batch in chunks(fresh, 50):
        try:
            col.insert_many(batch, ordered=False)
        except Exception:
            for d in batch:
                try:
                    col.insert_one(d)
                except Exception:
                    pass

    after = {str(d["_id"]) for d in col.find({}, projection={"_id": 1})}
    inserted = len(after - existing_ids)
    skipped = len([d for d in docs if d.get("_id")]) - inserted
    return inserted, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["1", "2", "3", "all"], default="1")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true", help="actually write to live DB")
    ap.add_argument("--images", action="store_true", help="upload profile images to Cloudinary")
    args = ap.parse_args()

    global DO_UPLOADS
    DO_UPLOADS = bool(args.images and args.yes)

    vals = load_env(ENV)
    rt = json.loads(RT_JSON.read_text(encoding="utf-8")) if RT_JSON.exists() else {}
    fs = {p.stem: json.loads(p.read_text(encoding="utf-8"))
          for p in FS_DIR.glob("*.json") if p.stem != "manifest"}

    plan, stats = build(fs, rt, args.images)
    print("BUILD:", stats)

    main_db = connect(vals, aux=False)
    aux_db = connect(vals, aux=True)

    targets = {"1": PHASE1, "2": PHASE2, "3": PHASE3, "all": PHASE1 + PHASE2 + PHASE3}[args.phase]
    for name in targets:
        group, docs = plan[name]
        print("  %-28s %-4s %6d docs" % (name, group, len(docs)))
        if args.dry_run or not args.yes:
            continue
        db = aux_db if group == "aux" else main_db
        ins, skip = import_group(db, name, docs, is_users=(name == "users"))
        print("      -> inserted %d, skipped %d" % (ins, skip))

    if args.dry_run or not args.yes:
        print("DRY-RUN complete (no writes). Pass --yes to import phase %s." % args.phase)


if __name__ == "__main__":
    main()
