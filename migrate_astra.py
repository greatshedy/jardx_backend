"""JardX Astra DB migration tool.

Copies every JardX collection from the OLD Astra DB account (Backend/.env)
to the NEW account (Backend/.env.new), preserving each document's _id so all
cross-collection string references (user_id, house_id, portfolio_id, ...) stay intact.

Subcommands:
  preflight   prove _id-preserving writes work on the NEW account
  export      dump all collections from the OLD account into migration_dump/
  import      create collections and upsert all documents into the NEW account
  verify      compare counts, _id sets and referential integrity old vs new
  all         export + import + verify

Nothing in this tool ever writes to the OLD account.
"""

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

from dotenv import dotenv_values
from astrapy import DataAPIClient
from astrapy.api_options import APIOptions, TimeoutOptions

BASE = Path(__file__).resolve().parent
OLD_ENV = BASE / ".env"
NEW_ENV = BASE / ".env.new"
DUMP = BASE / "migration_dump"

MAIN_COLLECTIONS = [
    "users",
    "jard_houses",
    "payment_transactions",
    "portfolio",
    "jard_kidz_plans",
    "vendors",
    "partners",
    "products",
    "orders",
    "reviews",
    "notifications",
]
AUX_COLLECTIONS = [
    "reports",
    "subscription_settings",
    "withdraw_requests",
    "deletion_requests",
]

USER_LINKED = [
    "payment_transactions",
    "portfolio",
    "orders",
    "notifications",
    "jard_kidz_plans",
    "vendors",
    "partners",
    "reviews",
]

REQUIRED_KEYS = ["DB_URL", "DB_TOKEN", "DB_AUX_URL", "DB_AUX_TOKEN"]


def load_env(path):
    vals = dotenv_values(str(path))
    missing = [k for k in REQUIRED_KEYS if not vals.get(k) or not str(vals[k]).strip()]
    if missing:
        raise SystemExit("ERROR: %s is missing values for: %s" % (path, ", ".join(missing)))
    return vals


def connect(vals, aux=False):
    prefix = "DB_AUX" if aux else "DB"
    client = DataAPIClient(
        vals[prefix + "_TOKEN"],
        api_options=APIOptions(timeout_options=TimeoutOptions(request_timeout_ms=60000)),
    )
    return client.get_database_by_api_endpoint(vals[prefix + "_URL"])


def chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def count(db, name):
    return db.get_collection(name).count_documents({}, upper_bound=1000000)


def upsert(col, docs):
    for doc in docs:
        col.delete_one({"_id": doc["_id"]})
    for batch in chunks(docs, 50):
        col.insert_many(batch, ordered=False)


def preflight():
    vals = load_env(NEW_ENV)
    db = connect(vals, aux=False)
    name = "_jardx_migtest"
    if name in db.list_collection_names():
        db.get_collection(name).drop()
        time.sleep(1)
    col = db.create_collection(name)
    try:
        doc_id = str(uuid.uuid4())
        col.insert_one({"_id": doc_id, "v": 1, "nested": {"items": [1, 2, 3]}})
        assert col.find_one({"_id": doc_id}) is not None
        assert col.find_one({"_id": uuid.UUID(doc_id)}) is not None
        print("insert_one + find by string/uuid _id: OK")

        col.replace_one({"_id": doc_id}, {"_id": doc_id, "v": 2}, upsert=True)
        print("replace_one upsert with _id: OK (v=%s)" % col.find_one({"_id": doc_id}).get("v"))

        more = [{"_id": str(uuid.uuid4()), "v": 10}, {"_id": str(uuid.uuid4()), "v": 11}]
        col.insert_many(more, ordered=False)
        print("insert_many with explicit string _id: OK")

        col.delete_many({"_id": {"$in": [d["_id"] for d in more]}})
        print("delete_many by _id $in: OK")

        print("PREFLIGHT PASSED")
    finally:
        db.get_collection(name).drop()
        print("dropped test collection %s" % name)


def export():
    old = load_env(OLD_ENV)
    DUMP.mkdir(parents=True, exist_ok=True)
    manifest = {}
    plan = [("main", connect(old, aux=False), MAIN_COLLECTIONS),
            ("aux", connect(old, aux=True), AUX_COLLECTIONS)]
    for group, db, collections in plan:
        outdir = DUMP / group
        outdir.mkdir(parents=True, exist_ok=True)
        available = set(db.list_collection_names())
        for cname in collections:
            if cname not in available:
                manifest["%s/%s" % (group, cname)] = 0
                print("export %s/%s: collection missing in OLD, wrote empty" % (group, cname))
                (outdir / ("%s.json" % cname)).write_text("[]", encoding="utf-8")
                continue
            docs = list(db.get_collection(cname).find({}))
            (outdir / ("%s.json" % cname)).write_text(
                json.dumps(docs, default=str, ensure_ascii=False), encoding="utf-8")
            manifest["%s/%s" % (group, cname)] = len(docs)
            print("export %s/%s: %d docs" % (group, cname, len(docs)))
    (DUMP / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("EXPORT COMPLETE -> %s" % DUMP)
    return manifest


def import_all(dry_run=False):
    new = load_env(NEW_ENV)
    total = 0
    plan = [("main", connect(new, aux=False), MAIN_COLLECTIONS),
            ("aux", connect(new, aux=True), AUX_COLLECTIONS)]
    for group, db, collections in plan:
        available = set(db.list_collection_names())
        for cname in collections:
            path = DUMP / group / ("%s.json" % cname)
            if not path.exists():
                print("import %s/%s: SKIP (no dump file)" % (group, cname))
                continue
            docs = json.loads(path.read_text(encoding="utf-8"))
            print("import %s/%s: %d docs%s" % (group, cname, len(docs), " (dry-run)" if dry_run else ""))
            total += len(docs)
            if dry_run:
                continue
            if cname not in available:
                col = db.create_collection(cname)
            else:
                col = db.get_collection(cname)
            upsert(col, docs)
    print("IMPORT COMPLETE: %d docs%s" % (total, " (dry run, nothing written)" if dry_run else ""))
    return total


def _fk_report(label, new_main, new_aux):
    users = {str(d["_id"]) for d in new_main.get_collection("users").find({}, projection={"_id": 1})}
    problems = []
    for cname in USER_LINKED:
        ids = [str(d.get("user_id")) for d in new_main.get_collection(cname).find({}, projection={"user_id": 1})]
        orphan = sorted({i for i in ids if i and i not in users})
        if orphan:
            problems.append("%s.user_id -> %d orphan(s) e.g. %s" % (cname, len(orphan), orphan[:3]))
    for cname, field in (("withdraw_requests", "user_id"), ("reports", "reporter_id"), ("deletion_requests", "user_id")):
        ids = [str(d.get(field)) for d in new_aux.get_collection(cname).find({}, projection={field: 1})]
        orphan = sorted({i for i in ids if i and i not in users})
        if orphan:
            problems.append("aux %s.%s -> %d orphan(s) e.g. %s" % (cname, field, len(orphan), orphan[:3]))
    products = {str(d["_id"]) for d in new_main.get_collection("products").find({}, projection={"_id": 1})}
    for d in new_main.get_collection("orders").find({}, projection={"items": 1}):
        for item in d.get("items", []) or []:
            pid = str(item.get("product_id"))
            if pid and pid not in products:
                problems.append("orders.items.product_id -> orphan %s" % pid)
                break
    if problems:
        print("REFERENTIAL INTEGRITY: %d issue(s):" % len(problems))
        for p in problems:
            print("   - " + p)
    else:
        print("REFERENTIAL INTEGRITY: OK")
    return problems


def verify():
    old = load_env(OLD_ENV)
    new = load_env(NEW_ENV)
    old_main, old_aux = connect(old, aux=False), connect(old, aux=True)
    new_main, new_aux = connect(new, aux=False), connect(new, aux=True)
    ok = True
    plan = [("main", old_main, new_main, MAIN_COLLECTIONS),
            ("aux", old_aux, new_aux, AUX_COLLECTIONS)]
    for group, odb, ndb, collections in plan:
        for cname in collections:
            o_ids = {str(d["_id"]) for d in odb.get_collection(cname).find({}, projection={"_id": 1})}
            n_ids = {str(d["_id"]) for d in ndb.get_collection(cname).find({}, projection={"_id": 1})}
            missing = o_ids - n_ids
            extra = n_ids - o_ids
            status = "OK" if not missing and not extra else "MISMATCH"
            if status != "OK":
                ok = False
            print("%-8s %-22s old=%-4d new=%-4d missing=%-3d extra=%-3d %s"
                  % (group, cname, len(o_ids), len(n_ids), len(missing), len(extra), status))
    _fk_report("new", new_main, new_aux)
    print("VERIFY %s" % ("PASSED" if ok else "FAILED"))
    return ok


def main():
    parser = argparse.ArgumentParser(description="JardX Astra DB migration")
    parser.add_argument("command", choices=["preflight", "export", "import", "verify", "all"])
    parser.add_argument("--dry-run", action="store_true", help="for import: connect/read but do not write")
    args = parser.parse_args()

    if args.command == "preflight":
        preflight()
    elif args.command == "export":
        export()
    elif args.command == "import":
        import_all(dry_run=args.dry_run)
    elif args.command == "verify":
        sys.exit(0 if verify() else 1)
    elif args.command == "all":
        export()
        import_all()
        sys.exit(0 if verify() else 1)


if __name__ == "__main__":
    main()
