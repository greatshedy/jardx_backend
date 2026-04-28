import httpx
import os
from .base import BasePaymentGateway

class FlutterwaveGateway(BasePaymentGateway):
    def __init__(self):
        self.secret_key = os.getenv("FLUTTERWAVE_SECRET_KEY")
        self.base_url = "https://api.flutterwave.com/v3"

    async def initialize_payment(self, amount: float, user_email: str, reference: str, **kwargs):
        payload = {
            "tx_ref": reference,
            "amount": amount,
            "currency": "NGN",
            "redirect_url": kwargs.get("redirect_url", "jardx://addmoneysuccess"),
            "customer": {
                "email": user_email,
                "name": kwargs.get("user_name", "JardX User")
            },
            "customizations": {
                "title": "JardX Wallet Funding",
                "logo": "https://your-logo-url.com"
            }
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/payments",
                json=payload,
                headers={"Authorization": f"Bearer {self.secret_key}"}
            )
            data = response.json()
            if data.get("status") == "success":
                return {
                    "checkout_url": data["data"]["link"],
                    "reference": reference
                }
            raise Exception(f"Flutterwave Init Failed: {data.get('message')}")

    async def verify_transaction(self, reference: str):
        # In Flutterwave, we usually verify by ID, but can also use tx_ref via query params
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/transactions/verify_by_reference?tx_ref={reference}",
                headers={"Authorization": f"Bearer {self.secret_key}"}
            )
            return response.json()

    async def handle_webhook(self, payload: dict, signature: str, raw_body: bytes = None):
        # Flutterwave webhook verification: check if secret-hash in header matches
        expected_hash = os.getenv("FLUTTERWAVE_WEBHOOK_HASH") # You should set this in your dashboard
        if signature == expected_hash:
            return payload.get("status") == "successful"
        return False
