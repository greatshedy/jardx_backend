"""Phase 0 - back up the LIVE (.env.new) Astra collections before importing.

Read-only against Astra: dumps each target collection to
Backend/migration_dump_live/<group>/<collection>.json so we can roll back.

Usage:  python backup_live.py
"""
import json
from pathlib import Path

from dotenv import dotenv_values
from astrapy import DataAPIClient
from astrapy.api_options import APIOptions, TimeoutOptions

BASE = Path(__file__).resolve().parent
ENV = BASE / ".env.new"
OUT = BASE / "migration_dump_live"

MAIN_COLLECTIONS = [
    "users", "jard_houses", "payment_transactions", "portfolio",
    "jard_kidz_plans", "vendors", "partners",
]
AUX_COLLECTIONS = ["withdraw_requests"]
REQUIRED = ["DB_URL", "DB_TOKEN", "DB_AUX_URL", "DB_AUX_TOKEN"]


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


def dump(group, db, names):
    outdir = OUT / group
    outdir.mkdir(parents=True, exist_ok=True)
    available = set(db.list_collection_names())
    manifest = {}
    for name in names:
        if name not in available:
            print("  %s/%s: missing (write empty)" % (group, name))
            (outdir / ("%s.json" % name)).write_text("[]", encoding="utf-8")
            manifest[name] = 0
            continue
        docs = list(db.get_collection(name).find({}))
        (outdir / ("%s.json" % name)).write_text(
            json.dumps(docs, default=str, ensure_ascii=False), encoding="utf-8")
        manifest[name] = len(docs)
        print("  %s/%-22s %6d docs" % (group, name, len(docs)))
    return manifest


if __name__ == "__main__":
    vals = load_env(ENV)
    print("Backing up LIVE (.env.new) collections ...")
    main = connect(vals, aux=False)
    aux = connect(vals, aux=True)
    manifest = {}
    manifest["main"] = dump("main", main, MAIN_COLLECTIONS)
    manifest["aux"] = dump("aux", aux, AUX_COLLECTIONS)
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("BACKUP COMPLETE ->", OUT)
