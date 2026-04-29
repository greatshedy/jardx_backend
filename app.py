from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import os
import time
import threading
import requests
from routes import users, admin, payment, finance
from routes import portfolio



import logging

# Use absolute path to ensure .env is loaded regardless of current working directory
current_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(current_dir, ".env"))

from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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
    return {
        "message": "JardX Backend is running",
        "status": "healthy",
        "timestamp": time.time()
    }


# Mount static files
app.mount("/static", StaticFiles(directory=os.path.join(current_dir, "static")), name="static")

# Ensure uploads directory exists for profile pictures and houses
UPLOAD_DIR = os.path.join(current_dir, "uploads")
os.makedirs(os.path.join(UPLOAD_DIR, "profiles"), exist_ok=True)
os.makedirs(os.path.join(UPLOAD_DIR, "houses"), exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


# Templates
templates = Jinja2Templates(directory=os.path.join(current_dir, "templates"))
app.state.templates = templates

# CORS configuration
allowed_origins_raw = os.getenv("CORS_ALLOWED_ORIGINS", "*")
if allowed_origins_raw == "*":
    origins = ["*"]
    allow_credentials = False
else:
    origins = [origin.strip() for origin in allowed_origins_raw.split(",")]
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
    """
    Endpoint for Render/UptimeRobot to check if the app and DB are alive.
    """
    try:
        # Import inside to avoid circular dependency if any, 
        # though not an issue with current structure.
        from db.database import user_collection
        # Simple query to verify Astra DB connectivity
        user_collection.find_one({})
        return {"status": "healthy", "database": "connected", "timestamp": time.time()}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {"status": "unhealthy", "database": str(e)}

def keep_alive_loop():
    """
    Background loop to ping the server and prevent hibernation.
    """
    # Wait a bit for the server to fully start
    time.sleep(10)
    
    url = os.getenv("BACKEND_BASE_URL")
    if not url:
        logger.info("Keep-alive: Skipping (URL not set)")
        return
    
    # Only skip if we are explicitly in a local environment that isn't the one we want to ping
    # On Render, the URL will be the production one, so it should NOT skip.
    if ("localhost" in url or "127.0.0.1" in url) and not os.getenv("RENDER"):
        logger.info(f"Keep-alive: Skipping (Local environment detected: {url})")
        return


    logger.info(f"Keep-alive: Starting loop for {url}")
    while True:
        try:
            # Ping the health endpoint
            target = f"{url.rstrip('/')}/health"
            response = requests.get(target, timeout=15)
            logger.info(f"Keep-alive ping to {target}: {response.status_code}")
        except Exception as e:
            logger.error(f"Keep-alive ping failed: {e}")
        
        # Ping every 10 minutes (Render hibernates after 15 mins of inactivity)
        time.sleep(600)

@app.on_event("startup")
async def startup_event():
    # Run the keep-alive loop in a separate background thread
    threading.Thread(target=keep_alive_loop, daemon=True).start()
