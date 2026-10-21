import argparse
import datetime

from db.database import house_collection

PROPERTY_TYPES = ("residential", "residential-commercial", "full_commercial")
WISDOM_COURT_ID = "4950d6b0-7f96-4eaf-90d6-b07f962eaf28"
WISDOM_COURT_PRICES = {
    "residential": 2500000.0,
    "residential-commercial": 3500000.0,
    "full_commercial": 5000000.0,
}


def normalize_property_type(value):
    normalized = str(value or "").strip().lower().replace("_", " ").replace("-", " ")
    if normalized == "residential":
        return "residential"
    if normalized == "residential commercial":
        return "residential-commercial"
    if normalized in {"full commercial", "fully commercial", "commercial"}:
        return "full_commercial"
    return normalized.replace(" ", "-")


def infer_plan_property_type(plan):
    explicit = normalize_property_type(plan.get("propertyType") or plan.get("property_type"))
    if explicit in PROPERTY_TYPES:
        return explicit
    name = str(plan.get("name") or "").strip().lower()
    if "residential commercial" in name or "residential-commercial" in name:
        return "residential-commercial"
    if "full commercial" in name or "fully commercial" in name or "full_commercial" in name:
        return "full_commercial"
    if "residential" in name:
        return "residential"
    return ""


def make_plan(property_type, price, template=None):
    template = template or {}
    return {
        "id": f"migrated-{property_type}-{int(datetime.datetime.utcnow().timestamp() * 1000)}",
        "propertyType": property_type,
        "unitSqm": template.get("unitSqm") or template.get("unit_sqm") or "",
        "outrightPrice": str(price),
        "price": str(price),
        "percentageIncrease": template.get("percentageIncrease") or 0,
        "numInstallments": template.get("numInstallments") or [],
        "downPayment": template.get("downPayment") or 0,
    }


def find_plan(plans, property_type):
    for plan in plans:
        if infer_plan_property_type(plan) == property_type:
            return plan
    return None


def migrate_house(house, dry_run=True):
    original = house
    plans = house.get("house_pricing_plan")
    if not isinstance(plans, list):
        plans = []
    plans = [dict(plan) for plan in plans]
    template = plans[0] if plans else {}
    changed = False

    for plan in plans:
        inferred = infer_plan_property_type(plan)
        if inferred and normalize_property_type(plan.get("propertyType") or plan.get("property_type")) != inferred:
            plan["propertyType"] = inferred
            changed = True

    legacy_prices = house.get("property_type_prices") or {}
    if isinstance(legacy_prices, dict):
        for property_type in PROPERTY_TYPES:
            try:
                price = float(legacy_prices.get(property_type) or legacy_prices.get(property_type.replace("-", "_")) or 0)
            except (TypeError, ValueError):
                price = 0
            if price <= 0:
                continue
            plan = find_plan(plans, property_type)
            if plan is None:
                plans.append(make_plan(property_type, price, template))
                changed = True
            else:
                try:
                    current_price = float(plan.get("outrightPrice") or plan.get("price") or 0)
                except (TypeError, ValueError):
                    current_price = 0
                if current_price <= 0:
                    plan["propertyType"] = property_type
                    plan["outrightPrice"] = str(price)
                    plan["price"] = str(price)
                    changed = True

    if str(house.get("_id")) == WISDOM_COURT_ID:
        for property_type, price in WISDOM_COURT_PRICES.items():
            plan = find_plan(plans, property_type)
            if plan is None:
                plans.append(make_plan(property_type, price, template))
                changed = True
            else:
                try:
                    current_price = float(plan.get("outrightPrice") or plan.get("price") or 0)
                except (TypeError, ValueError):
                    current_price = 0
                if current_price <= 0:
                    plan["propertyType"] = property_type
                    plan["outrightPrice"] = str(price)
                    plan["price"] = str(price)
                    changed = True

        synced_prices = {}
        for property_type, fallback_price in WISDOM_COURT_PRICES.items():
            plan = find_plan(plans, property_type)
            try:
                synced_prices[property_type] = float(
                    (plan or {}).get("outrightPrice") or (plan or {}).get("price") or fallback_price
                )
            except (TypeError, ValueError):
                synced_prices[property_type] = fallback_price
        if house.get("property_type_prices") != synced_prices:
            house["property_type_prices"] = synced_prices
            changed = True

    if changed:
        house["house_pricing_plan"] = plans

    return changed, house


def main():
    parser = argparse.ArgumentParser(description="Backfill pricing-plan property types.")
    parser.add_argument("--apply", action="store_true", help="Write changes to the database.")
    args = parser.parse_args()
    dry_run = not args.apply

    houses = list(house_collection.find({}))
    changed_count = 0
    for house in houses:
        changed, updated = migrate_house(house, dry_run=dry_run)
        if not changed:
            continue
        changed_count += 1
        name = house.get("house_name", "Unnamed")
        print(f"{'WOULD UPDATE' if dry_run else 'UPDATING'}: {name} ({house.get('_id')})")
        if dry_run:
            print(f"  plans: {len(updated.get('house_pricing_plan') or [])}")
            print(f"  property_type_prices: {updated.get('property_type_prices')}")
        else:
            house_collection.update_one(
                {"_id": house.get("_id")},
                {"$set": {
                    "house_pricing_plan": updated.get("house_pricing_plan"),
                    "property_type_prices": updated.get("property_type_prices"),
                }},
            )

    print(f"{'Dry run complete' if dry_run else 'Migration complete'}: {changed_count} estate(s) {'would be' if dry_run else ''} updated.")
    if dry_run:
        print("Run with --apply to write the changes.")


if __name__ == "__main__":
    main()
