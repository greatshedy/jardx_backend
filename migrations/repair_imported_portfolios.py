"""Conservatively repair imported non-block investment relationships.

This script is intentionally strict: it only remaps a portfolio or JardKidz plan
when a single legacy UID maps to a single email, that email maps to exactly one
live user, and a house can be matched unambiguously (or via an explicit alias).

It never touches Jard Block records.

Usage:
    python migrations/repair_imported_portfolios.py
    python migrations/repair_imported_portfolios.py --apply

The default mode is a dry run that only prints the proposed changes.
"""
import argparse
import datetime
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from dotenv import dotenv_values
from astrapy import DataAPIClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = BACKEND_ROOT / ".env"
FS_DIR = BACKEND_ROOT / "firebase_dump" / "firestore"
RT_JSON = Path(r"C:\Users\NEW USER\Downloads\jarvard firebase realtime db.json")

USER_COLLECTIONS = ["users"]
SOURCE_COLLECTIONS = [
    "estatePurchases",
    "installmentPlans",
    "kidvestInvestments",
    "blockInvestments",
    "pendingBlockInvestments",
]
PORTFOLIO_COLLECTION = "portfolio"
KIDZ_COLLECTION = "jard_kidz_plans"

# Explicit aliases for imported titles that do not exactly match a live house.
HOUSE_ALIASES = {
    "ebenezery estate": "ebenezery-estate",
    "wisdom court estate": "4950d6b0-7f96-4eaf-90d6-b07f962eaf28",
    "jarvad estate": "jarvad-estate-isaga",
    "jarvad estate easter promo": "jarvad-estate-ayetoro",
    "land banking mini estate entrepreneurs": "land-banking-mini-estate",
    "joyhills estate": "joyhills-estate",
}


def norm_email(value):
    return str(value or "").strip().lower()


def norm_name(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()


def load_json(path):
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def load_env(path):
    values = dotenv_values(str(path))
    required = ["DB_URL", "DB_TOKEN"]
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise SystemExit("ERROR: %s missing %s" % (path, ", ".join(missing)))
    return values


def connect(values):
    client = DataAPIClient(values["DB_TOKEN"])
    return client.get_database_by_api_endpoint(values["DB_URL"])


def build_legacy_email_map(fs):
    """Legacy Firebase UID -> set of normalized emails from every safe source."""
    mapping = defaultdict(set)

    for name in USER_COLLECTIONS:
        for user in load_json(FS_DIR / (name + ".json")):
            uid = str(user.get("_id") or user.get("uid") or "").strip()
            email = norm_email(user.get("email"))
            if uid and email:
                mapping[uid].add(email)

    if RT_JSON.exists():
        realtime = load_json(RT_JSON)
        for uid, record in (realtime.get("users") or {}).items():
            email = norm_email(((record or {}).get("profile") or {}).get("email"))
            if uid and email:
                mapping[str(uid)].add(email)

    for name in SOURCE_COLLECTIONS:
        for doc in load_json(FS_DIR / (name + ".json")):
            uid = str(doc.get("userId") or doc.get("uid") or "").strip()
            email = norm_email(
                doc.get("userEmail")
                or doc.get("email")
                or doc.get("investorEmail")
            )
            if uid and email:
                mapping[uid].add(email)

    return mapping


def resolve_user(doc, live_ids, live_by_email, legacy_email_map):
    current = str(doc.get("user_id") or "").strip()
    if current and current in live_ids:
        return current, "", "linked"

    emails = set()
    direct_email = norm_email(doc.get("user_email"))
    if direct_email:
        emails.add(direct_email)
    emails.update(legacy_email_map.get(current, set()))

    if not emails:
        return "", "", "unresolved-no-email"
    if len(emails) > 1:
        return "", "", "ambiguous-multiple-emails"

    email = next(iter(emails))
    matches = live_by_email.get(email, [])
    if not matches:
        return "", email, "unresolved-no-live-user"
    if len(matches) > 1:
        return "", email, "ambiguous-multiple-live-users"
    return str(matches[0].get("_id")), email, "repairable"


def house_maps(houses):
    by_name = defaultdict(list)
    by_id = {}
    for house in houses:
        house_id = str(house.get("_id") or "")
        by_id[house_id] = house
        key = norm_name(house.get("house_name"))
        if key:
            by_name[key].append(house)
    return by_id, by_name


def resolve_house(doc, live_house_ids, houses_by_name):
    current = str(doc.get("house_id") or "").strip()
    if current and current in live_house_ids:
        return current, "linked"

    title = norm_name(doc.get("house_name"))
    if not title:
        return "", "unresolved-no-title"

    alias = HOUSE_ALIASES.get(title)
    if alias:
        return (alias, "alias") if alias in live_house_ids else ("", "alias-missing")

    candidates = houses_by_name.get(title, [])
    if len(candidates) == 1:
        return str(candidates[0].get("_id")), "repairable"
    if len(candidates) > 1:
        return "", "ambiguous-multiple-houses"
    return "", "unresolved-no-house"


def plan_patch(doc, live_ids, live_by_email, legacy_email_map, live_house_ids, houses_by_name, now):
    patch = {}
    target_user, email, user_status = resolve_user(doc, live_ids, live_by_email, legacy_email_map)
    current_user = str(doc.get("user_id") or "").strip()

    if target_user and target_user != current_user:
        patch["user_id"] = target_user
        if current_user and not doc.get("legacy_user_id"):
            patch["legacy_user_id"] = current_user
        if email:
            patch["user_email"] = email
        patch["repaired_at"] = now

    house_status = "not-applicable"
    if doc.get("house_id") is not None or doc.get("house_name"):
        target_house, house_status = resolve_house(doc, live_house_ids, houses_by_name)
        current_house = str(doc.get("house_id") or "").strip()
        if target_house and target_house != current_house:
            patch["house_id"] = target_house
            if current_house and not doc.get("legacy_house_id"):
                patch["legacy_house_id"] = current_house
            patch["house_repaired_at"] = now

    return patch, target_user, email, user_status, house_status


def print_plan_item(kind, doc, target_user, email, user_status, house_status):
    print(
        "  %-9s %-28s user=%-30s email=%-32s user_status=%-26s house_status=%s"
        % (
            kind,
            str(doc.get("_id"))[:28],
            (target_user or "-")[:30],
            (email or "-")[:32],
            user_status,
            house_status,
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write changes. Default is dry-run.")
    args = parser.parse_args()

    values = load_env(ENV_PATH)
    db = connect(values)
    users = list(db.get_collection("users").find({}))
    houses = list(db.get_collection("jard_houses").find({}))
    portfolios = list(db.get_collection(PORTFOLIO_COLLECTION).find({}))
    kidz = list(db.get_collection(KIDZ_COLLECTION).find({}))

    live_ids = {str(user.get("_id")) for user in users if user.get("_id")}
    live_by_email = defaultdict(list)
    for user in users:
        email = norm_email(user.get("email"))
        if email:
            live_by_email[email].append(user)

    live_house_ids, houses_by_name = house_maps(houses)
    legacy_email_map = build_legacy_email_map({})
    now = datetime.datetime.utcnow().isoformat()
    stats = Counter()
    unresolved = []

    for kind, docs in (("portfolio", portfolios), ("kidz", kidz)):
        collection = db.get_collection(PORTFOLIO_COLLECTION if kind == "portfolio" else KIDZ_COLLECTION)
        for doc in docs:
            patch, target_user, email, user_status, house_status = plan_patch(
                doc, live_ids, live_by_email, legacy_email_map,
                live_house_ids, houses_by_name, now,
            )
            if user_status == "repairable":
                stats[kind + "_user_repairable"] += 1
            elif user_status.startswith("ambiguous") or user_status.startswith("unresolved"):
                stats[kind + "_user_unresolved"] += 1
                unresolved.append((kind, str(doc.get("_id")), user_status, email, doc.get("house_name") or doc.get("child_name")))
            else:
                stats[kind + "_user_linked"] += 1

            if kind == "portfolio":
                if house_status in ("repairable", "alias"):
                    stats["portfolio_house_repairable"] += 1
                elif house_status.startswith("ambiguous") or house_status.startswith("unresolved"):
                    stats["portfolio_house_unresolved"] += 1
                    unresolved.append((kind + "-house", str(doc.get("_id")), house_status, doc.get("house_name"), ""))
                elif house_status == "linked":
                    stats["portfolio_house_linked"] += 1

            if patch:
                print_plan_item(kind, doc, target_user, email, user_status, house_status)
                if args.apply:
                    collection.update_one({"_id": doc.get("_id")}, {"$set": patch})
                    stats[kind + "_docs_updated"] += 1

    print("\nSUMMARY")
    for key in sorted(stats):
        print("  %-36s %s" % (key, stats[key]))
    print("  %-36s %s" % ("unresolved_total", len(unresolved)))
    if unresolved:
        print("\nUNRESOLVED / NEEDS MANUAL REVIEW")
        for item in unresolved:
            print("  ", item)
    print("\nMODE:", "APPLIED" if args.apply else "DRY RUN")


if __name__ == "__main__":
    main()
