import asyncio
import json
from routes.users import Listing

async def test():
    try:
        response = await Listing()
        data = json.loads(response.body.decode())
        print(json.dumps(data, indent=2))
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test())
