import requests

def test_pagination():
    base_url = "http://127.0.0.1:8000/admin/finance/transactions"
    
    print("Testing Page 1 (size 2)...")
    r1 = requests.get(f"{base_url}?page=1&page_size=2")
    data1 = r1.json()
    print(f"Status: {r1.status_code}")
    print(f"Pagination: {data1.get('pagination')}")
    print(f"Items: {len(data1.get('data', []))}")

    if data1.get('pagination', {}).get('total_pages', 0) > 1:
        print("\nTesting Page 2 (size 2)...")
        r2 = requests.get(f"{base_url}?page=2&page_size=2")
        data2 = r2.json()
        print(f"Status: {r2.status_code}")
        print(f"Pagination: {data2.get('pagination')}")
        print(f"Items: {len(data2.get('data', []))}")
        
        # Check if they are different
        if data1.get('data') and data2.get('data'):
            if data1['data'][0]['_id'] != data2['data'][0]['_id']:
                print("\nSUCCESS: Pages have different data.")
            else:
                print("\nFAILURE: Pages have the same data.")

if __name__ == "__main__":
    test_pagination()
