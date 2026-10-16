"""Repair users after Phase 1 import:
 1. Overlay migrated balances/referral fields onto pre-existing live accounts
    that were skipped due to email collision.
 2. Remap any referred_by that points to a skipped (collided) uid so it resolves
    to the live account _id with the same email.

Idempotent and additive: only fills blanks / zero balances, never lowers them.
Run:  python repair_users.py
"""
import json
import sys
from pathlib import Path

from dotenv import dotenv_values
from astrapy import DataAPIClient
from astrapy.api_options import APIOptions, TimeoutOptions

sys.path.insert(0, str(Path(__file__).resolve().parent))
import migrate_firebase_to_astra as M

v = dotenv_values(str(M.ENV))
cl = DataAPIClient(v["DB_TOKEN"], api_options=APIOptions(timeout_options=TimeoutOptions(request_timeout_ms=90000)))
db = cl.get_database_by_api_endpoint(v["DB_URL"])
col = db.get_collection("users")

fs = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in M.FS_DIR.glob("*.json") if p.stem != "manifest"}
rt = json.loads(M.RT_JSON.read_text(encoding="utf-8"))
plan, _ = M.build(fs, rt, False)
planned = plan["users"][1]

live = list(col.find({}))
live_ids = {str(d["_id"]) for d in live}
live_by_email = {}
for d in live:
    e = (d.get("email") or "").lower()
    if e and e not in live_by_email:
        live_by_email[e] = d

# 1) remap map: skipped planned uid -> existing live _id (same email)
remap = {}
for u in planned:
    if u["_id"] in live_ids:
        continue
    e = (u.get("email") or "").lower()
    if e and e in live_by_email:
        remap[u["_id"]] = str(live_by_email[e]["_id"])
print("collided planned uids:", len(remap))

# 2) overlay balances / fields onto the existing live accounts
overlays = 0
for u in planned:
    if u["_id"] not in remap:
        continue
    ex = live_by_email[(u.get("email") or "").lower()]
    patch = {}
    if float(ex.get("wallet_balance") or 0) == 0 and float(u.get("wallet_balance") or 0) > 0:
        patch["wallet_balance"] = u["wallet_balance"]
    if float(ex.get("earnings_balance") or 0) == 0 and float(u.get("earnings_balance") or 0) > 0:
        patch["earnings_balance"] = u["earnings_balance"]
    for f in ("referral_code", "profile_pic", "phone_number", "referred_by"):
        if not ex.get(f) and u.get(f):
            patch[f] = u[f]
    patch["migrated"] = True
    patch["migrated_at"] = u.get("migrated_at", M.NOW)
    col.update_one({"_id": ex["_id"]}, {"$set": patch})
    overlays += 1
print("overlaid accounts:", overlays)

# 3) remap referred_by that points to a skipped uid
fixed = 0
if remap:
    keys = list(remap.keys())
    for d in list(col.find({"referred_by": {"$in": keys}}, projection={"_id": 1, "referred_by": 1})):
        col.update_one({"_id": d["_id"]}, {"$set": {"referred_by": remap[d["referred_by"]]}})
        fixed += 1
print("referred_by remapped:", fixed)

# report remaining orphans
live2 = list(col.find({}, projection={"_id": 1, "referred_by": 1}))
ids = {str(d["_id"]) for d in live2}
orph = [d for d in live2 if d.get("referred_by") and d["referred_by"] not in ids]

# 4) last resort: referrer uid -> email -> live account with that email
uid_to_email = {u.get("_id"): (u.get("email") or "").lower() for u in fs.get("users", [])}
for uid, rec in (rt.get("users", {}) or {}).items():
    e = ((rec.get("profile") or {}).get("email") or "").lower()
    if e:
        uid_to_email.setdefault(uid, e)
fixed2 = 0
for d in orph:
    e = uid_to_email.get(d["referred_by"])
    if e and e in live_by_email and str(live_by_email[e]["_id"]) != str(d["_id"]):
        col.update_one({"_id": d["_id"]}, {"$set": {"referred_by": str(live_by_email[e]["_id"])}})
        fixed2 += 1
print("second-pass referred_by remapped:", fixed2)

live3 = list(col.find({}, projection={"_id": 1, "referred_by": 1}))
ids3 = {str(d["_id"]) for d in live3}
orph2 = [d for d in live3 if d.get("referred_by") and d["referred_by"] not in ids3]
print("remaining referred_by orphans:", len(orph2))
for d in orph2[:10]:
    print("   ", str(d["_id"])[:16], "->", d["referred_by"])
