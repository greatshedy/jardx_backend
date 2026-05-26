from fastapi.testclient import TestClient
import sys
import os

# Adjust path to import app
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import app

client = TestClient(app)

print("Verifying /users/vendors endpoint...")
response = client.get("/users/vendors")
print(f"Status Code: {response.status_code}")
if response.status_code == 200:
    data = response.json()
    print("Success! Response JSON:")
    import json
    print(json.dumps(data, indent=2))
else:
    print(f"Failed! Response content: {response.text}")
