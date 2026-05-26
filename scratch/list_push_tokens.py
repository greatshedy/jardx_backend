import sys
import os
import json

# Add backend directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import user_collection

def list_tokens():
    try:
        # Fetch users with a push_token field
        users = list(user_collection.find({}))
        found_any = False
        for user in users:
            token = user.get("push_token")
            email = user.get("email")
            username = user.get("user_name")
            user_id = str(user.get("_id"))
            if token:
                print(f"User ID: {user_id} | Name: {username} | Email: {email} | Push Token: {token}")
                found_any = True
        
        if not found_any:
            print("No users found with a registered push token.")
            print("\nTotal users in database:")
            for user in users[:10]:
                print(f"- {user.get('user_name')} ({user.get('email')}) [Has token: {'push_token' in user}]")
    except Exception as e:
        print(f"Error querying database: {e}")

if __name__ == "__main__":
    list_tokens()
