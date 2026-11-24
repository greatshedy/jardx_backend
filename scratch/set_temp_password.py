"""One-off: set a temporary password for a single user account.

Hashes with the same Argon2 PasswordHasher settings used by utill.hashedpassword
and updates only the password (plus OTP lock fields) in the `users` collection.

Usage (from Backend/):
    python scratch/set_temp_password.py --dry-run
    python scratch/set_temp_password.py
"""

import argparse
import secrets
from pathlib import Path

from argon2 import PasswordHasher
from astrapy import DataAPIClient
from dotenv import dotenv_values

EMAIL = "kunlewise2077@gmail.com"

BACKEND_DIR = Path(__file__).resolve().parents[1]
env = dotenv_values(BACKEND_DIR / ".env")
if not env.get("DB_TOKEN") or not env.get("DB_URL"):
    raise SystemExit("DB_TOKEN / DB_URL missing from Backend/.env")

client = DataAPIClient(env["DB_TOKEN"])
db = client.get_database_by_api_endpoint(env["DB_URL"])
users = db.get_collection("users")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--password", default=None, help="Override the generated temp password")
    args = parser.parse_args()

    matches = list(
        users.find(
            {"email": EMAIL},
            projection={"email": 1, "user_name": 1, "status": 1, "password": 1},
        )
    )

    print(f"Matches for {EMAIL}: {len(matches)}")
    for m in matches:
        has_pw = bool((m.get("password") or "").strip())
        print(
            f"  _id={m.get('_id')} name={m.get('user_name')!r} "
            f"status={m.get('status')!r} has_password={has_pw}"
        )

    if len(matches) != 1:
        raise SystemExit("Expected exactly one match; aborting without changes.")

    if args.dry_run:
        print("Dry run: no changes made.")
        return

    user = matches[0]
    temp_password = args.password or secrets.token_urlsafe(9)
    ph = PasswordHasher()
    new_hash = ph.hash(temp_password)
    if not ph.verify(new_hash, temp_password):
        raise SystemExit("Local hash verification failed; aborting.")

    result = users.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "password": new_hash,
                "otp": "",
                "otp_block_until": None,
                "otp_resend_count": 0,
            }
        },
    )
    print(f"Update result: {result.update_info}")

    stored = users.find_one({"_id": user["_id"]}, projection={"password": 1})
    if not stored or not ph.verify(stored["password"], temp_password):
        raise SystemExit("Post-update verification failed.")

    print("Password updated and verified.")
    print(f"TEMP_PASSWORD={temp_password}")


if __name__ == "__main__":
    main()
