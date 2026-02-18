import os
from dotenv import load_dotenv

# Load .env file
load_dotenv()

class Config:
    # --- MongoDB Settings ---
    MONGO_URI = os.getenv("MONGO_URI")
    DB_NAME = os.getenv("DB_NAME", "yugen_hub") # Defaults to yugen_hub, can be overridden in .env

    # --- Security Settings ---
    # In production, ALWAYS set this in .env. Never use the fallback.
    SECRET_KEY = os.getenv("SECRET_KEY", "dev_secret_key_change_in_prod")
    ALGORITHM = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7 # 7 Days

    # --- Google OAuth Settings ---
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")

    # --- Environment ---
    ENV = os.getenv("ENV", "development") # "development" or "production"

config = Config()
