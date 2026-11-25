"""One-off backfill for migrated portfolio records.

Uses the original Firebase dumps to fill missing totals, schedules and
months-paid fields on the live `portfolio` collection, and links exact
estate-purchase transactions by tx_ref.

Usage (from Backend/):
    python backfill_portfolio_migration.py            # dry run
    python backfill_portfolio_migration.py --yes      # apply
"""

import argparse
import datetime
import json
import tempfile
from pathlib import Path

from db.database import portfolio_collection, transactions_collection

BACKEND_DIR = Path(__file__).resolve().parent
FIRESTORE_DIR = BACKEND_DIR / "firebase_dump" / "firestore"


def _load(name):
    path = FIRESTORE_DIR / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"Missing dump: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else list(data.values())


def _num(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()


def _normalize_status(value, fallback=None):
    text = str(value or "").strip().lower()
    if text in {"active", "ongoing"}:
        return "Active"
    if text in {"completed", "complete", "paid"}:
        return "Completed"
    if text in {"defaulted", "default"}:
        return "Defaulted"
    return fallback


def _valid_iso(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return value.strip()
    except (TypeError, ValueError):
        return None


def _portfolio_patch(doc, source, source_name):
    patch = {}
    current_total = _num(doc.get("total_price"))
    current_paid = _num(doc.get("amount_paid"))
    current_months = int(doc.get("months_paid") or 0)

    source_total = _num(source.get("totalAmount") or source.get("totalPrice") or source.get("amount"))
    source_paid = _num(source.get("totalPaid") or source.get("amount"))

    if source_total > 0 and current_total <= 0:
        patch["total_price"] = source_total

    if source_paid > 0 and current_paid <= 0:
        patch["amount_paid"] = source_paid

    if source_name == "installmentPlans":
        months = int(_num(source.get("months")))
        installments = source.get("installments") or source.get("schedule") or []
        monthly = 0.0
        if isinstance(installments, list) and len(installments) > 1 and isinstance(installments[1], dict):
            monthly = _num(installments[1].get("amount"))
        if monthly <= 0 and months > 0:
            down = 0.0
            if isinstance(installments, list) and installments and isinstance(installments[0], dict):
                down = _num(installments[0].get("amount"))
            monthly = max(0.0, (source_total - down) / months)

        if months > 0 and int(doc.get("duration_months") or 0) <= 0:
            patch["duration_months"] = months
        if monthly > 0 and _num(doc.get("monthly_payment")) <= 0:
            patch["monthly_payment"] = round(monthly, 2)

        paid_installments = source.get("paidInstallments") or []
        if isinstance(paid_installments, list) and paid_installments:
            derived_months_paid = max(0, len(paid_installments) - 1)
            if current_months <= 0 and derived_months_paid > 0:
                patch["months_paid"] = derived_months_paid

        next_due = _valid_iso(source.get("nextDueDate"))
        if next_due and not doc.get("next_payment_date"):
            patch["next_payment_date"] = next_due

        remaining = _num(source.get("remainingAmount"))
        if remaining <= 0 and source_total > 0:
            remaining = max(0.0, source_total - max(current_paid, source_paid))
        if doc.get("remaining_balance") in (None, "") and remaining > 0:
            patch["remaining_balance"] = round(remaining, 2)

    if source_name == "estatePurchases" and source_total > 0:
        if doc.get("remaining_balance") in (None, ""):
            patch["remaining_balance"] = 0.0
        if "next_payment_date" not in doc or doc.get("next_payment_date") is None:
            patch["next_payment_date"] = ""
        if "duration_months" not in doc or doc.get("duration_months") is None:
            patch["duration_months"] = 0
        if "monthly_payment" not in doc or doc.get("monthly_payment") is None:
            patch["monthly_payment"] = 0.0
        if "months_paid" not in doc or doc.get("months_paid") is None:
            patch["months_paid"] = 0
        if str(doc.get("status") or "").strip().lower() != "completed":
            patch["status"] = "Completed"
        if current_paid <= 0:
            patch["amount_paid"] = source_total
        elif source_total > current_paid:
            patch["amount_paid"] = source_total

    status = _normalize_status(source.get("status"), doc.get("status"))
    if status and status != doc.get("status"):
        patch["status"] = status

    created = _valid_iso(source.get("createdAt"))
    if created and not _valid_iso(doc.get("created_at")):
        patch["created_at"] = created

    if not doc.get("user_email") and source.get("userEmail"):
        patch["user_email"] = str(source.get("userEmail")).strip()
    if not doc.get("user_name") and source.get("userName"):
        patch["user_name"] = str(source.get("userName")).strip()

    if patch:
        patch["migrated_backfilled_at"] = _now_iso()
    return patch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="Apply changes (default is dry-run)")
    parser.add_argument("--backup", default=None, help="Path for the JSON backup file")
    args = parser.parse_args()

    sources = {
        "installmentPlans": _load("installmentPlans"),
        "estatePurchases": _load("estatePurchases"),
    }
    by_source = {
        name: {str(item.get("_id")): item for item in docs if isinstance(item, dict) and item.get("_id")}
        for name, docs in sources.items()
    }

    portfolios = [
        doc for doc in portfolio_collection.find({})
        if str(doc.get("source_collection") or "") in by_source
    ]

    changes = []
    transaction_links = []
    backup = {"created_at": _now_iso(), "portfolios": [], "transactions": []}

    for doc in portfolios:
        source_name = str(doc.get("source_collection"))
        source = by_source[source_name].get(str(doc.get("_id")))
        if not source:
            continue
        patch = _portfolio_patch(doc, source, source_name)
        if patch:
            changes.append((doc, patch))
            backup["portfolios"].append(dict(doc))

        if source_name == "estatePurchases":
            tx_refs = [ref for ref in [source.get("txRef")] if ref]
        else:
            tx_refs = [ref for ref in (source.get("walletRefs") or []) if ref]

        for ref in tx_refs:
            for tx in transactions_collection.find({"tx_ref": ref}):
                if tx.get("portfolio_id"):
                    continue
                transaction_links.append((tx.get("_id"), ref, str(doc.get("_id"))))
                backup["transactions"].append(dict(tx))

    print(f"Portfolios scanned: {len(portfolios)}")
    print(f"Portfolios needing updates: {len(changes)}")
    print(f"Transactions to link: {len(transaction_links)}")

    for doc, patch in changes[:10]:
        print(f"  {doc.get('_id')} | {doc.get('house_name')} | {doc.get('source_collection')} -> {patch}")

    if not args.yes:
        print("\nDry run only. Re-run with --yes to apply.")
        return

    backup_path = Path(args.backup) if args.backup else (
        Path(tempfile.gettempdir()) / f"jardx_portfolio_backfill_backup_{int(datetime.datetime.now(datetime.timezone.utc).timestamp())}.json"
    )
    backup_path.write_text(json.dumps(backup, indent=2, default=str), encoding="utf-8")
    print(f"Backup written: {backup_path}")

    applied = 0
    for doc, patch in changes:
        portfolio_collection.update_one({"_id": doc["_id"]}, {"$set": patch})
        applied += 1

    linked = 0
    for tx_id, _ref, portfolio_id in transaction_links:
        transactions_collection.update_one(
            {"_id": tx_id},
            {"$set": {"portfolio_id": portfolio_id}},
        )
        linked += 1

    print(f"Applied portfolio updates: {applied}")
    print(f"Applied transaction links: {linked}")


if __name__ == "__main__":
    main()
