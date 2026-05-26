import sys
import os
import json
import argparse

# Add backend directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import user_collection
from utils.push_service import send_push_notification

def get_registered_users():
    try:
        users = list(user_collection.find({}))
        registered = []
        for u in users:
            if u.get("push_token"):
                registered.append(u)
        return registered
    except Exception as e:
        print(f"Error fetching users: {e}")
        return []

def main():
    parser = argparse.ArgumentParser(description="JardX Push Notification Test Tool")
    parser.add_argument("--token", help="The recipient Expo Push Token (ExponentPushToken[xxx])")
    parser.add_argument("--title", help="Notification title")
    parser.add_argument("--body", help="Notification body")
    parser.add_argument("--payload", help="Custom JSON payload data for redirect/routing")
    parser.add_argument("--user-email", help="Fetch token for the user with this email from DB")
    
    args = parser.parse_args()

    print("=" * 60)
    print("        JARDX PUSH NOTIFICATION TEST TOOL")
    print("=" * 60)

    selected_token = None
    title = args.title
    body = args.body
    data_payload = {}

    # 1. Parse JSON payload if provided
    if args.payload:
        try:
            data_payload = json.loads(args.payload)
        except json.JSONDecodeError:
            print(f"Error: Invalid JSON string for --payload: {args.payload}")
            sys.exit(1)

    # 2. Determine target token
    if args.token:
        selected_token = args.token
        print(f"Using command-line token: {selected_token}")
    elif args.user_email:
        print(f"Fetching push token for user email: {args.user_email}")
        try:
            user = user_collection.find_one({"email": args.user_email})
            if user and user.get("push_token"):
                selected_token = user.get("push_token")
                print(f"Found token for {user.get('user_name') or args.user_email}: {selected_token}")
            else:
                print(f"Error: No push token found in database for email: {args.user_email}")
                sys.exit(1)
        except Exception as e:
            print(f"Error querying database: {e}")
            sys.exit(1)
    else:
        # Interactive mode
        print("\nFetching registered push tokens from database...")
        users = get_registered_users()
        
        if users:
            print(f"\nFound {len(users)} user(s) with registered push tokens:")
            for idx, u in enumerate(users, 1):
                print(f"[{idx}] {u.get('user_name')} ({u.get('email')})")
                print(f"    Token: {u.get('push_token')}")
            
            choice = input("\nSelect a user number to test, or press Enter to input a token manually: ").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(users):
                selected_token = users[int(choice) - 1].get("push_token")
                print(f"Selected token for {users[int(choice) - 1].get('user_name')}")
        else:
            print("\nNo registered push tokens found in the database.")
            print("You can get a push token by running the App on a physical device and checking the logs.")
            
        if not selected_token:
            selected_token = input("\nEnter Expo Push Token manually (e.g. ExponentPushToken[xxx]): ").strip()
            
        if not selected_token:
            print("\nError: No push token provided. Exiting.")
            sys.exit(1)

    # 3. Determine notification details
    if not args.token and not args.user_email:
        # We are in interactive mode, prompt for details
        if not title:
            title = input("\nEnter Notification Title [Default: Test Notification]: ").strip()
        if not body:
            body = input("Enter Notification Body [Default: This is a test push notification from JardX!]: ").strip()
            
        if not title:
            title = "Test Notification"
        if not body:
            body = "This is a test push notification from JardX!"

        screen = input("\nEnter redirect screen path (e.g. /notificationsettings, /housedetails) [Default: none]: ").strip()
        if screen:
            data_payload["screen"] = screen
            params_str = input("Enter screen params as JSON (e.g. {\"id\": \"123\"}) [Default: {}]: ").strip()
            if params_str:
                try:
                    data_payload["params"] = json.loads(params_str)
                except json.JSONDecodeError:
                    print("Invalid JSON for params. Ignoring params.")
    else:
        # Command-line mode
        if not title:
            title = "Test Notification"
        if not body:
            body = "This is a test push notification from JardX!"

    if not selected_token.startswith("ExponentPushToken"):
        print("\nWarning: Token does not start with 'ExponentPushToken'. This might fail.")
        
    print("\n" + "-" * 50)
    print("Dispatching push notification...")
    print(f"To Token:  {selected_token}")
    print(f"Title:     {title}")
    print(f"Body:      {body}")
    print(f"Payload:   {data_payload}")
    print("-" * 50)
    
    success = send_push_notification(selected_token, title, body, data_payload)
    
    if success:
        print("\nSUCCESS: Push notification successfully sent and accepted by Expo gateway!")
    else:
        print("\nFAILURE: Failed to send push notification. Check logs above for details.")
        
if __name__ == "__main__":
    main()
