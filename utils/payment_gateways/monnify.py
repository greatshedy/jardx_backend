import base64
import hashlib
import hmac
import logging
import os
from urllib.parse import quote

import httpx

from .base import BasePaymentGateway

logger = logging.getLogger("jardx")

LIVE_BASE_URL = "https://api.monnify.com"
SANDBOX_BASE_URL = "https://sandbox.monnify.com"
LIVE_MODES = {"live", "production", "prod"}
SANDBOX_MODES = {"test", "sandbox", "dev", "development"}

_mismatch_warned = False
_default_warned = False


def resolve_monnify_base_url():
    """Resolve the Monnify API base URL.

    `MONNIFY_BASE_URL` is treated as an explicit override so existing
    deployments cannot silently switch environment. If it is absent, the
    base URL is derived from `MONNIFY_MODE`.
    """
    global _mismatch_warned, _default_warned

    mode = (os.getenv("MONNIFY_MODE") or "").strip().lower()
    explicit = (os.getenv("MONNIFY_BASE_URL") or "").strip().rstrip("/")

    derived = None
    if mode in LIVE_MODES:
        derived = LIVE_BASE_URL
    elif mode in SANDBOX_MODES:
        derived = SANDBOX_BASE_URL

    if explicit:
        if derived and explicit != derived and not _mismatch_warned:
            logger.warning(
                "MONNIFY_MODE=%s resolves to %s but MONNIFY_BASE_URL=%s; using MONNIFY_BASE_URL",
                mode,
                derived,
                explicit,
            )
            _mismatch_warned = True
        return explicit

    if derived:
        return derived

    if not _default_warned:
        logger.warning(
            "Neither MONNIFY_MODE nor MONNIFY_BASE_URL is set; defaulting to Monnify sandbox"
        )
        _default_warned = True
    return SANDBOX_BASE_URL


def _safe_json(response):
    try:
        return response.json()
    except Exception:
        return {}


def _response_summary(response, data):
    return {
        "http_status": response.status_code,
        "response_code": data.get("responseCode"),
        "response_message": data.get("responseMessage") or (response.text or "")[:300],
        "response_body": data.get("responseBody"),
    }


class MonnifyGateway(BasePaymentGateway):
    def __init__(self):
        self.mode = (os.getenv("MONNIFY_MODE") or "").strip().lower()
        self.api_key = os.getenv("MONNIFY_API_KEY")
        self.secret_key = os.getenv("MONNIFY_SECRET")
        self.contract_code = os.getenv("MONNIFY_CONTRACT_CODE")
        self.base_url = resolve_monnify_base_url()

    def describe_config(self):
        """Safe config summary for startup logs (never includes secrets)."""
        return {
            "mode": self.mode or "unset",
            "base_url": self.base_url,
            "api_key_set": bool(self.api_key),
            "secret_set": bool(self.secret_key),
            "contract_code_set": bool(self.contract_code),
        }

    def _require_config(self):
        missing = [
            name
            for name, value in (
                ("MONNIFY_API_KEY", self.api_key),
                ("MONNIFY_SECRET", self.secret_key),
                ("MONNIFY_CONTRACT_CODE", self.contract_code),
            )
            if not value
        ]
        if missing:
            raise Exception(f"Monnify is not configured: missing {', '.join(missing)}")

    def _raise_gateway_error(self, action, response, data):
        summary = _response_summary(response, data)
        logger.error(
            "Monnify %s failed: status=%s code=%s message=%s body=%s",
            action,
            summary["http_status"],
            summary["response_code"],
            summary["response_message"],
            summary["response_body"],
        )
        raise Exception(
            "Monnify {action} failed (HTTP {status}, code={code}): {message}".format(
                action=action,
                status=summary["http_status"],
                code=summary["response_code"],
                message=summary["response_message"],
            )
        )

    async def _get_token(self):
        self._require_config()

        auth_str = f"{self.api_key}:{self.secret_key}"
        auth_b64 = base64.b64encode(auth_str.encode()).decode()

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/api/v1/auth/login",
                headers={"Authorization": f"Basic {auth_b64}"},
            )
            data = _safe_json(response)

        if response.status_code < 400 and data.get("requestSuccessful"):
            token = (data.get("responseBody") or {}).get("accessToken")
            if token:
                return token
            logger.error("Monnify auth returned no access token: %s", _response_summary(response, data))
            raise Exception("Monnify auth failed: empty access token")

        self._raise_gateway_error("auth", response, data)

    async def initialize_payment(self, amount: float, user_email: str, reference: str, **kwargs):
        self._require_config()
        token = await self._get_token()

        # Monnify strictly requires a valid HTTPS redirect URL.
        # We use our backend bridge to handle deep link redirection.
        custom_scheme_url = kwargs.get("redirect_url") or "jardx://addmoneysuccess"

        backend_base = os.getenv("BACKEND_BASE_URL")
        if not backend_base:
            backend_base = "http://localhost:8000"
        backend_base = backend_base.rstrip("/")

        if self.base_url == LIVE_BASE_URL and not backend_base.lower().startswith("https://"):
            logger.warning(
                "Monnify live requires an HTTPS redirect URL; BACKEND_BASE_URL=%s",
                backend_base,
            )

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
            "paymentMethods": ["CARD", "ACCOUNT_TRANSFER"],
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/api/v1/merchant/transactions/init-transaction",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            data = _safe_json(response)

        if response.status_code < 400 and data.get("requestSuccessful"):
            checkout_url = (data.get("responseBody") or {}).get("checkoutUrl")
            if checkout_url:
                return {"checkout_url": checkout_url, "reference": reference}
            logger.error(
                "Monnify init returned no checkout URL: %s",
                _response_summary(response, data),
            )
            raise Exception("Monnify init failed: empty checkout URL")

        self._raise_gateway_error("init", response, data)

    async def verify_transaction(self, reference: str):
        self._require_config()
        token = await self._get_token()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/api/v1/merchant/transactions/query?paymentReference={reference}",
                headers={"Authorization": f"Bearer {token}"},
            )
            data = _safe_json(response)

        if response.status_code >= 400 or not data.get("requestSuccessful"):
            logger.warning(
                "Monnify verify failed for %s: %s",
                reference,
                _response_summary(response, data),
            )

        return data

    async def handle_webhook(self, payload: dict, signature: str, raw_body: bytes = None):
        # Monnify webhook verification: SHA512(clientSecret|requestBody)
        if not self.secret_key or not raw_body or not signature:
            return False

        computed_sig = hmac.new(
            self.secret_key.encode(),
            raw_body,
            hashlib.sha512,
        ).hexdigest()

        signature_valid = hmac.compare_digest(signature.lower(), computed_sig.lower())
        return signature_valid and payload.get("paymentStatus") == "PAID"
