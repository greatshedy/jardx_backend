import requests

def debug_api():
    base_url = "http://127.0.0.1:8000/admin"
    
    print("Testing /finance/summary...")
    try:
        r = requests.get(f"{base_url}/finance/summary")
        print(f"Status Code: {r.status_code}")
        print(f"Response: {r.json()}")
    except Exception as e:
        print(f"Error: {e}")

    print("\nTesting /finance/transactions...")
    try:
        r = requests.get(f"{base_url}/finance/transactions")
        print(f"Status Code: {r.status_code}")
        # print(f"Response Data Length: {len(r.json().get('data', []))}")
        print(f"Full Response: {r.json()}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    debug_api()
