import os
import time
import threading
import requests
import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv
from routes import users, admin, payment, finance, portfolio, jardproc

# Use absolute path to ensure .env is loaded
current_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(current_dir, ".env"))

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: [%(asctime)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("jardx")

app = FastAPI()

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, HTMLResponse

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    logger.error(f"Validation error for {request.url}: {exc.errors()}")
    body_repr = str(exc.body) if exc.body else None
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": body_repr}
    )

def generate_app_redirect(params):
    type_ = params.get("type", "")
    sub_type = params.get("sub_type", "")
    name = params.get("name", "")
    amount = params.get("amount", "")
    auto_open = params.get("autoOpen") == "true"

    if type_ in ("kidz", "savings"):
        path = f"open?type={type_}&sub_type={sub_type}&name={name}"
    elif type_ == "payment":
        path = f"open?type=payment&amount={amount}"
    else:
        path = "open"

    redirect_url = f"jardx://{path}"

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Opening JardX...</title>
<style>body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#f8fafc;color:#1e293b;text-align:center;padding:20px}}
.card{{background:#fff;padding:40px;border-radius:20px;box-shadow:0 10px 15px -3px rgba(0,0,0,.1);max-width:400px;width:100%}}
h2{{margin:0 0 10px 0}}p{{color:#64748b;margin-bottom:20px;line-height:1.5}}
.btn{{display:inline-block;background:#FF6900;color:#fff;padding:16px 32px;border-radius:12px;text-decoration:none;font-weight:bold}}
.btn:hover{{opacity:.9}}
.loader{{border:3px solid #f3f3f3;border-top:3px solid #FF6900;border-radius:50%;width:24px;height:24px;animation:spin 1s linear infinite;display:inline-block;vertical-align:middle;margin-right:10px}}
@keyframes spin{{0%{{transform:rotate(0deg)}}100%{{transform:rotate(360deg)}}}}
</style></head><body><div class="card">
<h2>Opening JardX...</h2>
<p>You are being redirected to the JardX app.</p>
<a href="{redirect_url}" class="btn"><div class="loader"></div>Open JardX App</a>
</div>
<script>{"setTimeout(function(){window.location.href='" + redirect_url + "'},1000)" if auto_open else ""}</script>
</body></html>"""
    return HTMLResponse(content=html)


@app.get("/")
async def root(request: Request):
    params = request.query_params
    if params.get("type") or params.get("autoOpen"):
        return generate_app_redirect(params)
    try:
        return {
            "message": "JardX Backend is running",
            "status": "healthy",
            "timestamp": time.time()
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

# Mount static files
app.mount("/static", StaticFiles(directory=os.path.join(current_dir, "static")), name="static")

# Ensure uploads directory exists
UPLOAD_DIR = os.path.join(current_dir, "uploads")
os.makedirs(os.path.join(UPLOAD_DIR, "profiles"), exist_ok=True)
os.makedirs(os.path.join(UPLOAD_DIR, "houses"), exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# Templates
templates = Jinja2Templates(directory=os.path.join(current_dir, "templates"))
app.state.templates = templates

# --- Resilient CORS Configuration ---
allowed_origins_raw = os.getenv("CORS_ALLOWED_ORIGINS", "*")
if allowed_origins_raw == "*":
    origins = ["*"]
    allow_credentials = False
else:
    # Support multiple origins and handle trailing slashes
    origins = [origin.strip().rstrip('/') for origin in allowed_origins_raw.split(",")]
    allow_credentials = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(users.router)
app.include_router(admin.router)
app.include_router(payment.router)
app.include_router(finance.router)
app.include_router(portfolio.router)
app.include_router(jardproc.router)

# --- Keep-Alive & Health Check Logic ---
@app.get("/health")
async def health_check():
    try:
        from db.database import user_collection
        user_collection.find_one({})
        return {"status": "healthy", "database": "connected", "timestamp": time.time()}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {"status": "unhealthy", "database": "error", "error": str(e)}

def keep_alive_loop():
    time.sleep(15) # Wait for server startup
    url = os.getenv("BACKEND_BASE_URL")
    if not url:
        return
    
    if ("localhost" in url or "127.0.0.1" in url) and not os.getenv("RENDER"):
        return

    logger.info(f"Keep-alive: Started for {url}")
    while True:
        try:
            target = f"{url.rstrip('/')}/health"
            requests.get(target, timeout=10)
        except: pass
        time.sleep(600)

@app.on_event("startup")
async def startup_event():
    threading.Thread(target=keep_alive_loop, daemon=True).start()
