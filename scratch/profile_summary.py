import requests
import time

def profile_admin_summary():
    base_url = "http://127.0.0.1:8000/admin/finance/summary"
    print(f"Profiling {base_url}...")
    
    start = time.time()
    try:
        r = requests.get(base_url)
        end = time.time()
        print(f"Status: {r.status_code}")
        print(f"Response Time: {end - start:.2f} seconds")
        data = r.json().get('data', {})
        print(f"Revenue History: {data.get('revenue_history')}")
        print(f"Total Sales: {data.get('total_properties_revenue')}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    profile_admin_summary()
