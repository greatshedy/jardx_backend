import sys
import os

# Add backend directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import user_collection
from utill import send_push_notification_to_user

def run_test():
    print("=== Push Notification Toggle & Guard Test ===")
    
    # 1. Fetch all users
    users = list(user_collection.find({}))
    if not users:
        print("No users found in the database.")
        return
        
    print(f"Found {len(users)} users in database.\n")
    
    # 2. Find a test user (ideally the one with the push token from the prompt)
    target_user = None
    for u in users:
        if u.get("push_token"):
            target_user = u
            break
            
    if not target_user:
        # Fallback to first user
        target_user = users[0]
        print(f"Warning: No user with a push token found. Using first user: {target_user.get('user_name')}")
    else:
        print(f"Target User for Test:")
        print(f"  - Name: {target_user.get('user_name')}")
        print(f"  - Email: {target_user.get('email')}")
        print(f"  - Push Token: {target_user.get('push_token')}")
        
    # Check current switch setting
    settings = target_user.get("notification_settings", {})
    push_enabled = settings.get("push", False)
    print(f"  - Notification Switch is currently: {'ON (Enabled)' if push_enabled else 'OFF (Disabled)'}\n")
    
    # 3. Simulate Product/Estate Creation (Toggled OFF test)
    print("--- Test 1: Simulating new Product/Estate creation when toggle is OFF ---")
    # Temporarily set toggle to False in our local copy
    offline_user = dict(target_user)
    offline_user["notification_settings"] = {**settings, "push": False}
    
    print("Triggering push notification for a new product: 'Luxury Smart Watch'")
    success = send_push_notification_to_user(
        offline_user,
        title="New Product Alert!",
        body="We just added 'Luxury Smart Watch' to our store. Check it out now!",
        data_payload={"screen": "notifications", "image": "https://images.unsplash.com/photo-1523275335684-37898b6baf30?w=500"}
    )
    print(f"Result: Notification sent? {success} (Should be False because switch is OFF)")
    if not success:
        print("[SUCCESS]: Guard successfully prevented notification sending when toggle is OFF!")
    else:
        print("[FAIL]: Guard allowed sending notification even though toggle is OFF!")
        
    # 4. Simulate Product/Estate Creation (Toggled ON test)
    print("\n--- Test 2: Simulating new Product/Estate creation when toggle is ON ---")
    if not target_user.get("push_token"):
        print("Skipping active test: target user has no push token. Register a token on the device first!")
        return
        
    # Temporarily set toggle to True in our local copy
    online_user = dict(target_user)
    online_user["notification_settings"] = {**settings, "push": True}
    
    print("Triggering push notification for a new estate: 'JardX Heights Estate'")
    success = send_push_notification_to_user(
        online_user,
        title="New Real Estate Listed!",
        body="A premium property 'JardX Heights Estate' is now available in Lekki, Lagos.",
        data_payload={"screen": "notifications", "image": "https://images.unsplash.com/photo-1580587771525-78b9dba3b914?w=500"}
    )
    print(f"Result: Notification sent? {success} (Should be True if token is valid and active)")
    if success:
        print("[SUCCESS]: Push notification successfully sent and received by the device!")
    else:
        print("[INFO]: If sending failed, check if your Expo push token is active and valid.")
        
    # 5. Simulate In-App Notification Insertion & Fetch
    print("\n--- Test 3: Simulating In-App Notification database entry ---")
    from db.database import notifications_collection
    import datetime
    
    test_notif = {
        "user_id": str(target_user.get("_id")),
        "title": "Product Updated! 🛍️",
        "body": "The product 'JardX Heights Estate' has been updated with new details.",
        "type": "PRODUCT",
        "action_text": "View",
        "created_at": datetime.datetime.utcnow().isoformat(),
        "is_read": False,
        "image": "https://images.unsplash.com/photo-1580587771525-78b9dba3b914?w=500"
    }
    
    print(f"Inserting mock in-app notification for user {target_user.get('user_name')}...")
    notifications_collection.insert_one(test_notif)
    
    # Query it back
    fetched_notifs = list(notifications_collection.find({"user_id": str(target_user.get("_id"))}))
    print(f"Total in-app notifications in Astra DB for this user: {len(fetched_notifs)}")
    found = False
    for fn in fetched_notifs:
        if fn.get("title") == "Product Updated! 🛍️":
            print(f"[SUCCESS]: Successfully verified database insertion! Notification body: {fn.get('body')}")
            found = True
            break
            
    if not found:
        print("[FAIL]: Could not retrieve the inserted notification from database.")
 
if __name__ == "__main__":
    run_test()
