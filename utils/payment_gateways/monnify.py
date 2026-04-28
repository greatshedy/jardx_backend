import httpx
import os
import hashlib
import hmac
import base64
from .base import BasePaymentGateway

class MonnifyGateway(BasePaymentGateway):
    def __init__(self):
        self.api_key = os.getenv("MONNIFY_API_KEY")
        self.secret_key = os.getenv("MONNIFY_SECRET")
        self.contract_code = os.getenv("MONNIFY_CONTRACT_CODE")
        self.base_url = os.getenv("MONNIFY_BASE_URL", "https://sandbox.monnify.com")

    async def _get_token(self):
        auth_str = f"{self.api_key}:{self.secret_key}"
        auth_b64 = base64.b64encode(auth_str.encode()).decode()
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/api/v1/auth/login",
                headers={"Authorization": f"Basic {auth_b64}"}
            )
            data = response.json()
            if data.get("requestSuccessful"):
                return data["responseBody"]["accessToken"]
            raise Exception(f"Monnify Auth Failed: {data.get('responseMessage')}")

    async def initialize_payment(self, amount: float, user_email: str, reference: str, **kwargs):
        token = await self._get_token()

        # Monnify strictly requires a valid HTTPS redirect URL.
        # We now use our backend bridge to handle deep link redirection.
        from urllib.parse import quote
        custom_scheme_url = kwargs.get("redirect_url") or "jardx://addmoneysuccess"
        
        # Get backend base URL from environment
        backend_base = os.getenv("BACKEND_BASE_URL")
        if not backend_base:
             # Fallback to a safer logical default or raise if critical
             backend_base = "http://localhost:8000" 
        redirect_url = f"{backend_base}/users/payment/callback?redirect_url={quote(custom_scheme_url)}"

        payload = {
            "amount": amount,
            "customerName": kwargs.get("user_name", "JardX User"),
            "customerEmail": user_email,
            "paymentReference": reference,
            "paymentDescription": "Wallet Funding - JardX",
            "currencyCode": "NGN",
            "contractCode": self.contract_code,
            "redirectUrl": redirect_url,
            "paymentMethods": ["CARD", "ACCOUNT_TRANSFER"]
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/api/v1/merchant/transactions/init-transaction",
                json=payload,
                headers={"Authorization": f"Bearer {token}"}
            )
            data = response.json()
            if data.get("requestSuccessful"):
                return {
                    "checkout_url": data["responseBody"]["checkoutUrl"],
                    "reference": reference
                }
            raise Exception(f"Monnify Init Failed: {data.get('responseMessage')}")

    async def verify_transaction(self, reference: str):
        token = await self._get_token()
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/api/v1/merchant/transactions/query?paymentReference={reference}",
                headers={"Authorization": f"Bearer {token}"}
            )
            return response.json()

    async def handle_webhook(self, payload: dict, signature: str, raw_body: bytes = None):
        # Monnify webhook verification: SHA512(clientSecret|requestBody)
        if not raw_body:
            return False
            
        computed_sig = hmac.new(
            self.secret_key.encode(),
            raw_body,
            hashlib.sha512
        ).hexdigest()
        
        return signature == computed_sig and payload.get("paymentStatus") == "PAID"
