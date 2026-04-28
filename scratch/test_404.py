import httpx
import asyncio

async def test():
    token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6IjM1MmFmNzIxLTRjOGEtNDJjZC1hYWY3LTIxNGM4YTgyY2Q3MyIsImV4cCI6MTc3NjI1OTcyMn0.wtgX_jB3p4sPSdP3O_Sp4Sf8Xaf_3gsK2Wp9_74VcU8"
    
    # Test Portfolio (should be 200/401)
    url_port = "http://127.0.0.1:8000/users/portfolio/my-portfolio"
    
    # Test History (the one giving 404)
    url_hist = "http://127.0.0.1:8000/users/payment/history"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }
    
    async with httpx.AsyncClient() as client:
        try:
            r1 = await client.get(url_port, headers=headers)
            print(f"Portfolio: {r1.status_code}")
        except Exception as e:
            print(f"Portfolio Error: {e}")
            
        try:
            r2 = await client.get(url_hist, headers=headers)
            print(f"History: {r2.status_code}")
            if r2.status_code == 404:
                print("HISTORY RETURNED 404 ON LOCALHOST")
        except Exception as e:
            print(f"History Error: {e}")

asyncio.run(test())
