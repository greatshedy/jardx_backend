import httpx
import asyncio

async def test():
    token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6IjM1MmFmNzIxLTRjOGEtNDJjZC1hYWY3LTIxNGM4YTgyY2Q3MyIsImV4cCI6MTc3NjI1OTcyMn0.wtgX_jB3p4sPSdP3O_Sp4Sf8Xaf_3gsK2Wp9_74VcU8"
    url = "http://127.0.0.1:8000/users/portfolio/my-portfolio"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers)
            print(f"Status: {resp.status_code}")
            print(f"Body: {resp.text}")
    except Exception as e:
        print(f"Error: {e}")

asyncio.run(test())
