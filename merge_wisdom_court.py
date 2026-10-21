import argparse
import datetime
import json
import os

from db.database import house_collection

CANONICAL_ID = "4950d6b0-7f96-4eaf-90d6-b07f962eaf28"
DUPLICATE_ID = "wisdom-court-estate"
ORIGINAL_PRICES = {
    "residential": 5000000.0,
    "residential-commercial": 7000000.0,
    "full_commercial": 10000000.0,
}
PROMO_PRICES = {
    "residential": 2500000.0,
    "residential-commercial": 3500000.0,
    "full_commercial": 5000000.0,
}


def normalize_type(value):
    normalized = str(value or "").strip().lower().replace("_", " ").replace("-", " ")
    if normalized == "residential":
        return "residential"
    if normalized == "residential commercial":
        return "residential-commercial"
    if normalized in {"full commercial", "fully commercial", "commercial"}:
        return "full_commercial"
    return normalized.replace(" ", "-")


def merge_unique(first, second):
    result = []
    for item in list(first or []) + list(second or []):
        if item not in result:
            result.append(item)
    return result


def make_plan(property_type, original_price, template=None):
    template = template or {}
    return {
        "id": f"wisdom-{property_type}",
        "propertyType": property_type,
        "unitSqm": template.get("unitSqm") or "500",
        "outrightPrice": str(original_price),
        "price": str(original_price),
        "percentageIncrease": template.get("percentageIncrease") or 10,
        "numInstallments": template.get("numInstallments") or ["3", "6", "12", "24"],
        "downPayment": template.get("downPayment") or 30,
    }


def main():
    parser = argparse.ArgumentParser(description="Merge the duplicate Wisdom Court estate.")
    parser.add_argument("--apply", action="store_true", help="Write changes to the database.")
    args = parser.parse_args()

    canonical = house_collection.find_one({"_id": CANONICAL_ID})
    duplicate = house_collection.find_one({"_id": DUPLICATE_ID})
    if not canonical:
        raise SystemExit("Canonical Wisdom Court estate not found.")
    if not duplicate:
        print("Duplicate Wisdom Court estate already removed.")
        return

    backup_dir = os.environ.get("TEMP") or os.getcwd()
    backup_path = os.path.join(backup_dir, "wisdom_court_merge_backup.json")
    with open(backup_path, "w", encoding="utf-8") as handle:
        json.dump({"canonical": canonical, "duplicate": duplicate}, handle, default=str, indent=2)
    print(f"Backup written to {backup_path}")

    merged = dict(canonical)
    merged["house_image"] = merge_unique(canonical.get("house_image"), duplicate.get("house_image"))
    merged["house_benefits"] = merge_unique(canonical.get("house_benefits"), duplicate.get("house_benefits"))
    merged["house_landmarks"] = merge_unique(canonical.get("house_landmarks"), duplicate.get("house_landmarks"))
    merged["created_at"] = canonical.get("created_at") or duplicate.get("created_at")
    merged["source_pricing_options"] = duplicate.get("source_pricing_options") or canonical.get("source_pricing_options")

    existing_plans = canonical.get("house_pricing_plan") or []
    typed_plans = {}
    for plan in existing_plans:
        property_type = normalize_type(plan.get("propertyType") or plan.get("property_type"))
        if property_type in ORIGINAL_PRICES and property_type not in typed_plans:
            typed_plans[property_type] = dict(plan)

    template = existing_plans[0] if existing_plans else {}
    for property_type, original_price in ORIGINAL_PRICES.items():
        plan = typed_plans.get(property_type) or make_plan(property_type, original_price, template)
        plan["propertyType"] = property_type
        plan["outrightPrice"] = str(original_price)
        plan["price"] = str(original_price)
        typed_plans[property_type] = plan

    merged["house_pricing_plan"] = [
        typed_plans["residential"],
        typed_plans["residential-commercial"],
        typed_plans["full_commercial"],
    ]
    merged["property_type_prices"] = dict(ORIGINAL_PRICES)
    merged["house_is_promo"] = True
    merged["house_promo_type"] = "percentage"
    merged["house_promo_value"] = 50
    merged["promo_label"] = "Anniversary Special"
    merged["promo_visible"] = True
    merged["promo_applies_to"] = "land_estate"
    merged["promo_starts_at"] = ""
    merged["promo_ends_at"] = ""
    merged["promo_terms"] = ""
    merged["land_enabled"] = True
    merged["house_status"] = "Active"

    print("Merged result:")
    print(json.dumps({
        "house_name": merged.get("house_name"),
        "images": len(merged.get("house_image") or []),
        "benefits": merged.get("house_benefits"),
        "plans": [
            (plan.get("propertyType"), plan.get("outrightPrice"))
            for plan in merged.get("house_pricing_plan")
        ],
        "promo": {
            "label": merged.get("promo_label"),
            "type": merged.get("house_promo_type"),
            "value": merged.get("house_promo_value"),
            "applies_to": merged.get("promo_applies_to"),
        },
        "promo_prices": PROMO_PRICES,
    }, indent=2, default=str))

    if not args.apply:
        print("Dry run only. Run with --apply to merge and delete the duplicate.")
        return

    update_data = {
        key: merged.get(key)
        for key in [
            "house_image",
            "house_benefits",
            "house_landmarks",
            "created_at",
            "source_pricing_options",
            "house_pricing_plan",
            "property_type_prices",
            "house_is_promo",
            "house_promo_type",
            "house_promo_value",
            "promo_label",
            "promo_visible",
            "promo_applies_to",
            "promo_starts_at",
            "promo_ends_at",
            "promo_terms",
            "land_enabled",
            "house_status",
        ]
    }
    house_collection.update_one({"_id": CANONICAL_ID}, {"$set": update_data})
    house_collection.delete_one({"_id": DUPLICATE_ID})
    print(f"Merged into {CANONICAL_ID} and deleted {DUPLICATE_ID} at {datetime.datetime.utcnow().isoformat()}")


if __name__ == "__main__":
    main()
