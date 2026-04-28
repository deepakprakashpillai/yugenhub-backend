from google.oauth2 import id_token
from google.auth.transport import requests
from config import config
from logging_config import get_logger

logger = get_logger("google_verify")


def verify_google_id_token(token: str) -> dict | None:
    """Verify a Google ID token. Returns {email, name, picture, sub} or None on failure. NO DB writes."""
    try:
        info = id_token.verify_oauth2_token(token, requests.Request(), config.GOOGLE_CLIENT_ID)
        return {
            "email": info.get("email"),
            "name": info.get("name"),
            "picture": info.get("picture"),
            "sub": info.get("sub"),
        }
    except Exception as e:
        logger.warning(f"Google token verification failed: {e}")
        return None
