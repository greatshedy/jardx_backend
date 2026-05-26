import os
import sys
import json
import requests
from pymongo import MongoClient

# Load env variables (if using python-dotenv)
# from dotenv import load_dotenv
# load_dotenv()

# MongoDB connection (adjust connection string as needed)
MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017')
client = MongoClient(MONGO_URI)
db = client.get_default_database()
users_collection = db['users']

EXPO_PUSH_URL = 'https://exp.host/--/api/v2/push/send'

def send_test_push(push_token, title='Test Notification', body='This is a test notification from backend script.'):
    payload = {
        'to': push_token,
        'title': title,
        'body': body,
        # You can add data payload for navigation if needed
        'data': {'screen': 'Home'}
    }
    headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
    response = requests.post(EXPO_PUSH_URL, headers=headers, data=json.dumps(payload))
    try:
        resp_json = response.json()
    except Exception:
        resp_json = {'error': 'Invalid JSON response'}
    print('Expo push response:', resp_json)
    return resp_json

def main(user_id=None):
    if user_id:
        user = users_collection.find_one({'_id': user_id})
    else:
        # fallback: get first user with a push token
        user = users_collection.find_one({'push_token': {'$exists': True, '$ne': None}})
    if not user:
        print('No user with push token found.')
        return
    push_token = user.get('push_token')
    if not push_token:
        print('User does not have a push token.')
        return
    print(f'Sending test push to user {user.get("_id")} with token {push_token}')
    send_test_push(push_token)

if __name__ == '__main__':
    # Optional: pass user id as first argument
    uid = sys.argv[1] if len(sys.argv) > 1 else None
    main(uid)
