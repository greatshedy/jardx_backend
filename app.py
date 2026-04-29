import os
import time
import threading
import requests
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv
from routes import users, admin, payment, finance, portfolio

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

@app.get("/")
async def root():
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
