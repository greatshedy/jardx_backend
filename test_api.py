import requests
import json

url = "http://127.0.0.1:8000/users/home"
token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJqYXJ2YWRzaUBnbWFpbC5jb20iLCJleHAiOjE3NzgxNTQwMzV9.AqaIlYx-0HFSmy2UVLG4u8nu65CFj8bf1MAcvu9UYXw"

headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}

response = requests.post(url, headers=headers, json={})
print(f"Status: {response.status_code}")
print(f"Body: {json.dumps(response.json(), indent=2)}")
