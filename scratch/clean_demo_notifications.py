"""One-off cleanup: delete the 5 demo/seed notifications.

These were inserted by the old seeding block in routes/users.py
(`get_notifications`) the first time a user with no notifications opened the
screen. The block has been removed; this script removes the rows already in the
database. It matches BOTH the exact body and the fixed seed timestamp pattern
(`...T##:##:00Z`) so genuine notifications are never touched.

Run from the Backend directory:  python scratch/clean_demo_notifications.py
"""
import os
import re
from dotenv import load_dotenv
from astrapy import DataAPIClient

# Load Backend/.env regardless of the current working directory.
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_backend_dir, ".env"))

# Set to True to only preview what would be deleted.
DRY_RUN = False

DEMO_BODIES = [
    "Your wallet has been credited with ₦100,000.",
    "Your next payment for Ebenezery Estate is due in 8 days.",
    "You have an outstanding payment due for your property plan.",
    "We couldn't process your recent payment. Please try again.",
    "Your monthly JardKidz contribution is due tomorrow.",
]

# Seed rows used a fixed timestamp ending in :00Z, e.g. 2026-05-15T12:39:00Z.
SEED_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00Z$")


def main():
    client = DataAPIClient(os.getenv("DB_TOKEN"))
    db = client.get_database_by_api_endpoint(os.getenv("DB_URL"))
    notifications = db.get_collection("notifications")

    matches = [
        n
        for n in notifications.find({"body": {"$in": DEMO_BODIES}})
        if SEED_TIMESTAMP.match(str(n.get("created_at", "")))
    ]

    print(f"Found {len(matches)} matching demo notification(s).")
    for n in matches:
        print(f"  - user_id={n.get('user_id')} | {n.get('title')} | {n.get('body')} | {n.get('created_at')}")

    if DRY_RUN:
        print("\nDRY_RUN is on — nothing deleted.")
        return

    deleted = 0
    for n in matches:
        notifications.delete_one({"_id": n["_id"]})
        deleted += 1

    print(f"\nDeleted {deleted} demo notification(s).")


if __name__ == "__main__":
    main()
