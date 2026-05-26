import os
import sys
from dotenv import load_dotenv

# Add backend directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import user_collection, notifications_collection

sys.stdout.reconfigure(encoding='utf-8')

print("=== USERS IN DATABASE ===")
users = list(user_collection.find({}))
for u in users:
    print(f"ID: {u.get('_id')} | Name: {u.get('user_name')} | Email: {u.get('email')} | Token: {u.get('push_token')}")

print("\n=== NOTIFICATIONS IN DATABASE ===")
notifications = list(notifications_collection.find({}))
print(f"Total notifications: {len(notifications)}")
# Show latest
notifications.sort(key=lambda x: x.get("created_at", ""), reverse=True)
for n in notifications:
    print(f"ID: {n.get('_id')} | UserID: {n.get('user_id')} | Title: {n.get('title')} | Body: {n.get('body')} | CreatedAt: {n.get('created_at')}")
