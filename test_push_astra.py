import os
import sys
import json
import requests

# Use Astra DB connection utilities
from db.database import user_collection

EXPO_PUSH_URL = 'https://exp.host/--/api/v2/push/send'

def send_test_push(push_token, title='Test Notification', body='This is a test notification from backend script.', image_url=None):
    payload = {
        'to': push_token,
        'title': title,
        'body': body,
        'data': {'screen': 'Home'}
    }
    if image_url:
        payload['data']['image'] = image_url
        payload['mutableContent'] = True
        payload['richContent'] = {'image': image_url}
        
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
        user = user_collection.find_one({"_id": user_id})
    else:
        # Get first user with a push token
        user = user_collection.find_one({"push_token": {"$exists": True, "$ne": None}})
    if not user:
        print('No user with push token found.')
        return
    push_token = user.get('push_token')
    if not push_token:
        print('User does not have a push token.')
        return
    print(f'Sending test push to user {user.get("_id")} with token {push_token}')
    send_test_push(
        push_token=push_token,
        title="JardX Rich Test 🏡",
        body="This is a direct rich visual test notification with an attached property preview.",
        image_url="https://images.unsplash.com/photo-1580587771525-78b9dba3b914?w=500"
    )

if __name__ == '__main__':
    uid = sys.argv[1] if len(sys.argv) > 1 else None
    main(uid)
