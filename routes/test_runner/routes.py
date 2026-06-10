import os
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from .tests import TESTS, run_test

router = APIRouter(prefix="/admin/test-runner", tags=["Test Runner"])

templates_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "templates")
templates = Jinja2Templates(directory=templates_dir)

_results_cache = {}

@router.get("", response_class=HTMLResponse)
async def test_runner_page(request: Request):
    return templates.TemplateResponse(
        "test_runner/index.html",
        {"request": request, "tests": TESTS, "results": _results_cache}
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
