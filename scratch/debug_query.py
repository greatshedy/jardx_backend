from db.database import transactions_collection
import datetime
import calendar

month, year = 4, 2026
start = datetime.datetime(year, month, 1).isoformat()
last_day = calendar.monthrange(year, month)[1]
end = datetime.datetime(year, month, last_day, 23, 59, 59).isoformat()

query = {'user_id': '352af721-4c8a-42cd-aaf7-214c8a82cd73', 'created_at': {'$gte': start, '$lte': end}}
print(f"Query: {query}")
results = list(transactions_collection.find(query))
print(f"Count: {len(results)}")
if len(results) > 0:
    print(f"First result: {results[0].get('created_at')}")
else:
    # Try finding one without filter to see its format
    any_tx = transactions_collection.find_one()
    if any_tx:
        print(f"Format example: {any_tx.get('created_at')}")
