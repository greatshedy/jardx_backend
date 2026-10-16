"""Rollback the Firebase migration from the LIVE (.env.new) Astra database.

Uses the Phase-0 backup in migration_dump_live/ to restore the pre-import state:
 - non-user collections: delete every document flagged migrated=True
 - users: delete migrated docs we inserted; for pre-existing (overlaid) accounts,
   restore the original document from the backup.

Dry-run by default. Pass --yes to execute.
"""
import json
import time
from pathlib import Path

from dotenv import dotenv_values
from astrapy import DataAPIClient
from astrapy.api_options import APIOptions, TimeoutOptions

BASE = Path(__file__).resolve().parent
ENV = BASE / ".env.new"
BK = BASE / "migration_dump_live"

MAIN = ["users", "jard_houses", "payment_transactions", "portfolio",
        "jard_kidz_plans", "vendors", "partners"]
AUX = ["withdraw_requests", "referral_commissions", "referral_activities", "kids_profiles",
       "promos", "land_purchases", "land_payments", "land_referral_relationships",
       "support_messages"]


def connect(vals, aux=False):
    p = "DB_AUX" if aux else "DB"
    c = DataAPIClient(vals[p + "_TOKEN"],
                      api_options=APIOptions(timeout_options=TimeoutOptions(request_timeout_ms=120000)))
    return c.get_database_by_api_endpoint(vals[p + "_URL"])


def retry(fn, n=6):
    for i in range(n):
        try:
            return fn()
        except Exception as e:
            print("   retry:", str(e)[:60])
            time.sleep(4)
    return None


def load_backup(group, name):
    p = BK / group / (name + ".json")
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def rollback_collection(db, name, backup_docs, is_users, do):
    col = db.get_collection(name)
    migrated = retry(lambda: list(col.find({"migrated": True}, projection={"_id": 1, "email": 1}))) or []
    backup_ids = {str(d.get("_id")) for d in backup_docs}
    to_delete = [d for d in migrated if not (is_users and str(d["_id"]) in backup_ids)]
    to_restore = [d for d in backup_docs if is_users and str(d.get("_id")) in {str(m["_id"]) for m in migrated}]
    print("  %-28s migrated=%d -> delete=%d restore=%d" % (name, len(migrated), len(to_delete), len(to_restore)))
    if not do:
        return
    for d in to_delete:
        retry(lambda d=d: col.delete_one({"_id": d["_id"]}))
    for orig in to_restore:
        retry(lambda orig=orig: col.delete_one({"_id": orig["_id"]}))
        retry(lambda orig=orig: col.insert_one(orig))


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()
    do = args.yes
    vals = dotenv_values(str(ENV))
    main_db = connect(vals, aux=False)
    aux_db = connect(vals, aux=True)
    print("ROLLBACK %s" % ("(EXECUTE)" if do else "(dry-run)"))
    for name in MAIN:
        rollback_collection(main_db, name, load_backup("main", name), name == "users", do)
    for name in AUX:
        rollback_collection(aux_db, name, load_backup("aux", name), False, do)
    print("done%s" % ("" if do else " (pass --yes to execute)"))


if __name__ == "__main__":
    main()
