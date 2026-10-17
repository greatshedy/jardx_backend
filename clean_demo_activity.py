"""Clear the activity of the two demo accounts (keep the accounts).

Deletes transactions / portfolios / plans / notifications / referral + report
rows belonging to the demo user IDs, resets their balances, and clears any
`referred_by` that points at them. Orphan records are left untouched.

Backup-first. Dry-run by default; pass --yes to execute.

Run from the Backend directory:
  python clean_demo_activity.py            # dry-run (no writes)
  python clean_demo_activity.py --yes      # backup + delete
"""
import argparse
import json
import time
from pathlib import Path

from dotenv import dotenv_values
from astrapy import DataAPIClient
from astrapy.api_options import APIOptions, TimeoutOptions

BASE = Path(__file__).resolve().parent
ENV = BASE / ".env.new"
OUT = BASE / "migration_dump_preclean"
REQUIRED = ["DB_URL", "DB_TOKEN", "DB_AUX_URL", "DB_AUX_TOKEN"]

DEMO_EMAILS = ["greatboyshedy@gmail.com", "oluwatimilehinraphael22@gmail.com"]

# collection -> fields that may reference a user id
DELETE_FIELDS = {
    "payment_transactions": ["user_id"],
    "portfolio": ["user_id"],
    "jard_kidz_plans": ["user_id"],
    "notifications": ["user_id"],
    "withdraw_requests": ["user_id"],
    "deletion_requests": ["user_id"],
    "reports": ["reporter_id"],
    "referral_commissions": ["referrerId", "investorId"],
    "referral_activities": ["referrerId", "referredUserId"],
    "referral_relationships": ["referrerId", "referredUserId"],
    "land_referral_relationships": ["referrerId", "referredUserId"],
}
MAIN = {"payment_transactions", "portfolio", "jard_kidz_plans", "notifications"}
AUX = {"withdraw_requests", "deletion_requests", "reports", "referral_commissions",
       "referral_activities", "referral_relationships", "land_referral_relationships"}


def load_env(path):
    vals = dotenv_values(str(path))
    missing = [k for k in REQUIRED if not vals.get(k)]
    if missing:
        raise SystemExit("ERROR: %s missing %s" % (path, ", ".join(missing)))
    return vals


def connect(vals, aux=False):
    p = "DB_AUX" if aux else "DB"
    c = DataAPIClient(vals[p + "_TOKEN"],
                      api_options=APIOptions(timeout_options=TimeoutOptions(request_timeout_ms=120000)))
    return c.get_database_by_api_endpoint(vals[p + "_URL"])


def retry(fn, n=6):
    last = None
    for _ in range(n):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(4)
    raise last


def demo_ids(main_db):
    users = retry(lambda: list(main_db.get_collection("users").find(
        {}, projection={"_id": 1, "email": 1})))
    ids = [str(u["_id"]) for u in users if (u.get("email") or "").lower() in
           [e.lower() for e in DEMO_EMAILS]]
    return ids


def build_filter(fields, ids):
    if len(fields) == 1:
        return {fields[0]: {"$in": ids}}
    return {"$or": [{f: {"$in": ids}} for f in fields]}


def backup(db, names):
    OUT.mkdir(parents=True, exist_ok=True)
    available = set(retry(lambda: db.list_collection_names()))
    manifest = {}
    for name in names:
        if name not in available:
            continue
        docs = retry(lambda name=name: list(db.get_collection(name).find({})))
        (OUT / ("%s.json" % name)).write_text(
            json.dumps(docs, default=str, ensure_ascii=False), encoding="utf-8")
        manifest[name] = len(docs)
        print("  backed up %-26s %6d docs" % (name, len(docs)))
    return manifest


def run(do):
    vals = load_env(ENV)
    main_db = connect(vals, aux=False)
    aux_db = connect(vals, aux=True)

    ids = demo_ids(main_db)
    print("demo user ids:", ids)
    if not ids:
        print("No demo users found; nothing to do.")
        return

    # 1) Backup the collections we may touch (only when executing)
    manifest = {}
    if do:
        print("\nBacking up affected collections ->", OUT)
        manifest.update(backup(main_db, sorted(MAIN) + ["users"]))
        manifest.update(backup(aux_db, sorted(AUX)))
        (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # 2) Report / delete per collection
    print("\nDelete plan (user_id in demo ids):")
    for name, fields in DELETE_FIELDS.items():
        db = aux_db if name in AUX else main_db
        available = set(retry(lambda db=db: db.list_collection_names()))
        if name not in available:
            print("  %-26s (collection missing)" % name)
            continue
        col = db.get_collection(name)
        docs = retry(lambda col=col: list(col.find({}, projection={f: 1 for f in fields})))
        hits = [d for d in docs if any(str(d.get(f)) in ids for f in fields)]
        print("  %-26s %5d / %5d" % (name, len(hits), len(docs)))
        if do and hits:
            res = retry(lambda col=col, f=build_filter(fields, ids): col.delete_many(f))
            print("      deleted:", getattr(res, "deleted_count", "?"))

    # 3) Reset demo users' balances / stats
    if do:
        reset = {"wallet_balance": 0.0, "earnings_balance": 0.0, "total_deposits": 0,
                 "total_earnings": 0, "pendingCommission": 0}
        for uid in ids:
            retry(lambda uid=uid: main_db.get_collection("users").update_one(
                {"_id": uid}, {"$set": reset}))
        print("\nReset demo user balances/stats.")

    # 4) Clear referred_by that points at a demo account
    users = retry(lambda: list(main_db.get_collection("users").find(
        {}, projection={"_id": 1, "email": 1, "referred_by": 1})))
    ref = [u for u in users if str(u.get("referred_by")) in ids]
    print("\nusers with referred_by -> demo:", len(ref))
    for u in ref:
        print("   ", u.get("email"))
    if do:
        for u in ref:
            retry(lambda u=u: main_db.get_collection("users").update_one(
                {"_id": u["_id"]}, {"$set": {"referred_by": ""}}))
        print("   cleared referred_by for", len(ref), "users")

    print("\n%s" % ("DONE (executed)" if do else "DRY-RUN complete — pass --yes to execute"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()
    run(do=args.yes)
