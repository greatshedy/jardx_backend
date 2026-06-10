import time
import logging
from db.database import user_collection, house_collection, transactions_collection

logger = logging.getLogger("jardx")

TESTS = [
    {"id": "db-connection", "name": "DB Connection", "description": "Tests connectivity to user, house, and transaction collections"},
    {"id": "monnify-auth", "name": "Monnify Auth Token", "description": "Attempts to obtain an authentication token from Monnify sandbox"},
    {"id": "monnify-init", "name": "Monnify Init Payment", "description": "Initializes a test payment via Monnify (no DB save)"},
    {"id": "flutterwave-init", "name": "Flutterwave Init Payment", "description": "Initializes a test payment via Flutterwave (no DB save)"},
    {"id": "backend-health", "name": "Backend Health", "description": "Checks that all route modules and core utilities load correctly"},
]

async def test_db_connection():
    start = time.time()
    try:
        u = user_collection.count_documents({}, upper_bound=1)
        h = house_collection.count_documents({}, upper_bound=1)
        t = transactions_collection.count_documents({}, upper_bound=1)
        elapsed = round((time.time() - start) * 1000)
        return {
            "passed": True,
            "message": f"All collections accessible: users ({u}+), houses ({h}+), transactions ({t}+)",
            "details": {"users": "OK", "houses": "OK", "transactions": "OK"},
            "duration_ms": elapsed
        }
    except Exception as e:
        elapsed = round((time.time() - start) * 1000)
        return {
            "passed": False,
            "message": f"DB connection failed: {str(e)}",
            "details": {"error": str(e)},
            "duration_ms": elapsed
        }

async def test_monnify_auth():
    from utils.payment_gateways.monnify import MonnifyGateway
    start = time.time()
    try:
        gateway = MonnifyGateway()
        token = await gateway._get_token()
        elapsed = round((time.time() - start) * 1000)
        if token:
            masked = token[:10] + "..." + token[-5:] if len(token) > 15 else "present"
            return {
                "passed": True,
                "message": f"Monnify access token obtained successfully",
                "details": {"token_preview": masked},
                "duration_ms": elapsed
            }
        return {
            "passed": False,
            "message": "Monnify returned empty token",
            "details": {},
            "duration_ms": elapsed
        }
    except Exception as e:
        elapsed = round((time.time() - start) * 1000)
        return {
            "passed": False,
            "message": f"Monnify auth failed: {str(e)}",
            "details": {"error": str(e)},
            "duration_ms": elapsed
        }

async def test_monnify_init():
    from utils.payment_gateways.monnify import MonnifyGateway
    import secrets
    start = time.time()
    try:
        gateway = MonnifyGateway()
        ref = f"TEST-{secrets.token_hex(6).upper()}"
        result = await gateway.initialize_payment(
            amount=1000.0,
            user_email="test@jardx.com",
            reference=ref,
            user_name="Test User"
        )
        elapsed = round((time.time() - start) * 1000)
        return {
            "passed": bool(result.get("checkout_url")),
            "message": f"Checkout URL generated: {result.get('checkout_url', 'N/A')[:60]}...",
            "details": {"reference": ref, "checkout_url": result.get("checkout_url", "N/A")},
            "duration_ms": elapsed
        }
    except Exception as e:
        elapsed = round((time.time() - start) * 1000)
        return {
            "passed": False,
            "message": f"Monnify init failed: {str(e)}",
            "details": {"error": str(e)},
            "duration_ms": elapsed
        }

async def test_flutterwave_init():
    from utils.payment_gateways.flutterwave import FlutterwaveGateway
    import secrets
    start = time.time()
    try:
        gateway = FlutterwaveGateway()
        ref = f"TEST-FLW-{secrets.token_hex(6).upper()}"
        result = await gateway.initialize_payment(
            amount=1000.0,
            user_email="test@jardx.com",
            reference=ref,
            user_name="Test User"
        )
        elapsed = round((time.time() - start) * 1000)
        return {
            "passed": bool(result.get("checkout_url")),
            "message": f"Checkout URL generated: {result.get('checkout_url', 'N/A')[:60]}...",
            "details": {"reference": ref, "checkout_url": result.get("checkout_url", "N/A")},
            "duration_ms": elapsed
        }
    except Exception as e:
        elapsed = round((time.time() - start) * 1000)
        return {
            "passed": False,
            "message": f"Flutterwave init failed: {str(e)}",
            "details": {"error": str(e)},
            "duration_ms": elapsed
        }

async def test_backend_health():
    start = time.time()
    issues = []
    try:
        import routes.users as _
        import routes.admin as _
        import routes.payment as _
        import routes.portfolio as _
        import routes.finance as _
        import routes.jardproc as _
        from utill import get_token, process_referral_logic
        from model import PropertyPurchase, PortfolioModel
    except Exception as e:
        issues.append(str(e))
    elapsed = round((time.time() - start) * 1000)
    if not issues:
        return {
            "passed": True,
            "message": "All route modules and core utilities loaded successfully",
            "details": {"modules_loaded": ["users", "admin", "payment", "portfolio", "finance", "jardproc"]},
            "duration_ms": elapsed
        }
    return {
        "passed": False,
        "message": f"Module load issues: {'; '.join(issues)}",
        "details": {"errors": issues},
        "duration_ms": elapsed
    }

TEST_MAP = {
    "db-connection": test_db_connection,
    "monnify-auth": test_monnify_auth,
    "monnify-init": test_monnify_init,
    "flutterwave-init": test_flutterwave_init,
    "backend-health": test_backend_health,
}

async def run_test(test_id: str):
    fn = TEST_MAP.get(test_id)
    if not fn:
        return {
            "passed": False,
            "message": f"Unknown test: {test_id}",
            "details": {},
            "duration_ms": 0
        }
    logger.info(f"Test Runner: Running {test_id}...")
    return await fn()
