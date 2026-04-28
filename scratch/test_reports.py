import requests

def test_reports():
    url = "http://127.0.0.1:8000/admin/finance/reports"
    print(f"Testing reports endpoint: {url}")
    try:
        r = requests.get(url)
        print(f"Status: {r.status_code}")
        print(f"Full Response: {r.text}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_reports()
