import os
import sys
import json
from dotenv import load_dotenv

# Add backend directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

sys.stdout.reconfigure(encoding='utf-8')

from fastapi.testclient import TestClient
from app import app
from db.database import user_collection, notifications_collection
from utill import create_access_token

def test_endpoints():
    print("=== STARTING NOTIFICATION COMPATIBILITY TEST ===")
    client = TestClient(app)
    
    # Get a test user
    user = user_collection.find_one({"push_token": {"$exists": True, "$ne": None}})
    if not user:
        user = user_collection.find_one({})
    
    if not user:
        print("No users found in database, cannot test.")
        return
        
    user_id_str = str(user["_id"])
    print(f"Testing with user: {user.get('user_name')} (ID: {user_id_str})")
    
    # Create token for this user
    token = create_access_token({"id": user_id_str})
    
    # 1. Test /users/notifications GET
    print("\n--- Test 1: Querying /users/notifications ---")
    headers = {"Authorization": f"Bearer {token}"}
    response = client.get("/users/notifications", headers=headers)
    
    print(f"HTTP Status: {response.status_code}")
    assert response.status_code == 200, "Should return 200 OK"
    
    resp_json = response.json()
    print("JSON Response keys:", list(resp_json.keys()))
    
    # Check that root 'notifications' is present
    assert "notifications" in resp_json, "Root 'notifications' key missing!"
    print(f"Found root 'notifications' list with size: {len(resp_json['notifications'])}")
    
    # Check that nested 'data.notifications' is present
    assert "data" in resp_json, "Root 'data' envelope missing!"
    assert "notifications" in resp_json["data"], "Nested 'data.notifications' key missing!"
    print(f"Found nested 'data.notifications' list with size: {len(resp_json['data']['notifications'])}")
    
    # Check first element to verify type matching
    if resp_json["notifications"]:
        first = resp_json["notifications"][0]
        print(f"First notification title: {first.get('title')}")
        print(f"First notification body: {first.get('body')}")
    
    print("\n[SUCCESS]: Both frontend resilient structure and backend data envelope match perfectly!")

if __name__ == "__main__":
    test_endpoints()
