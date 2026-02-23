import os
from dotenv import load_dotenv

# Load .env file
load_dotenv()

class Config:
    # --- MongoDB Settings ---
    MONGO_URI = os.getenv("MONGO_URI")
    DB_NAME = os.getenv("DB_NAME", "yugen_hub") # Defaults to yugen_hub, can be overridden in .env

    # --- Environment ---
    ENV = os.getenv("ENV", "development") # "development" or "production"
    FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

    # --- Security Settings ---
    # In production, ALWAYS set this in .env. Never use the fallback.
    SECRET_KEY = os.getenv("SECRET_KEY")
    if ENV == "production" and not SECRET_KEY:
        raise ValueError("SECRET_KEY environment variable is mandatory in production!")
    elif not SECRET_KEY:
        SECRET_KEY = "dev_secret_key_change_in_prod"
    ALGORITHM = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7 # 7 Days

    # --- Google OAuth Settings ---
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")

    # --- Email Settings (Resend) ---
    RESEND_API_KEY = os.getenv("RESEND_API_KEY")
    MAIL_FROM = os.getenv("MAIL_FROM", "team@yugenco.in")

config = Config()
