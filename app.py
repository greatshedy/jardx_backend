from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import os
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

# Mount static files
app.mount("/static", StaticFiles(directory=os.path.join(current_dir, "static")), name="static")

# Ensure uploads directory exists for profile pictures
UPLOAD_DIR = os.path.join(current_dir, "uploads")
os.makedirs(os.path.join(UPLOAD_DIR, "profiles"), exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# Templates
templates = Jinja2Templates(directory=os.path.join(current_dir, "templates"))
app.state.templates = templates

# CORS configuration
allowed_origins_raw = os.getenv("CORS_ALLOWED_ORIGINS", "*")
origins = [origin.strip() for origin in allowed_origins_raw.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,  # Set to True if you need to send cookies/auth headers
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(users.router)
app.include_router(admin.router)
app.include_router(payment.router)
app.include_router(finance.router)

app.include_router(portfolio.router)
