import hmac
import os

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from .tests import TESTS, run_test

templates_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "templates")
templates = Jinja2Templates(directory=templates_dir)

_results_cache = {}

_basic_auth = HTTPBasic(auto_error=False)
_AUTH_HEADER = {"WWW-Authenticate": 'Basic realm="JardX Test Runner"'}


def verify_test_runner(credentials: HTTPBasicCredentials = Depends(_basic_auth)):
    """Require HTTP Basic credentials when the test runner is enabled."""
    expected_user = os.getenv("TEST_RUNNER_USER", "")
    expected_pass = os.getenv("TEST_RUNNER_PASS", "")

    if not expected_user or not expected_pass:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Test runner credentials are not configured",
        )

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers=_AUTH_HEADER,
        )

    user_ok = hmac.compare_digest(credentials.username.encode(), expected_user.encode())
    pass_ok = hmac.compare_digest(credentials.password.encode(), expected_pass.encode())

    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers=_AUTH_HEADER,
        )


router = APIRouter(
    prefix="/admin/test-runner",
    tags=["Test Runner"],
    dependencies=[Depends(verify_test_runner)],
)


@router.get("", response_class=HTMLResponse)
async def test_runner_page(request: Request):
    return templates.TemplateResponse(
        "test_runner/index.html",
        {"request": request, "tests": TESTS, "results": _results_cache},
    )


@router.get("/run/{test_id}", response_class=RedirectResponse)
async def run_single(test_id: str):
    if test_id == "all":
        for t in TESTS:
            _results_cache[t["id"]] = await run_test(t["id"])
    else:
        _results_cache[test_id] = await run_test(test_id)
    return RedirectResponse(url="/admin/test-runner", status_code=303)


@router.get("/clear", response_class=RedirectResponse)
async def clear_results():
    _results_cache.clear()
    return RedirectResponse(url="/admin/test-runner", status_code=303)
