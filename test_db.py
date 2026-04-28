from db.database import portfolio_collection, user_collection
import json

def list_portfolios():
    ports = list(portfolio_collection.find({}))
    for item in ports:
        item["_id"] = str(item["_id"])
    print(json.dumps(ports, indent=2))

if __name__ == "__main__":
    list_portfolios()
