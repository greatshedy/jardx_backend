import requests

def test_admin_users():
    base_url = "http://127.0.0.1:8000/admin/users"
    try:
        r = requests.get(base_url)
        print(f"Status: {r.status_code}")
        data = r.json()
        print(f"Message: {data.get('message')}")
        users = data.get('data', [])
        print(f"Users found: {len(users)}")
        if users:
            print(f"First user keys: {users[0].keys()}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_admin_users()
