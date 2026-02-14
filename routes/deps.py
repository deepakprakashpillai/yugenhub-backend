from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from datetime import datetime, timedelta
from typing import Optional
from database import users_collection
from models.user import UserModel
from logging_config import get_logger
import os
from dotenv import load_dotenv

load_dotenv()

logger = get_logger("auth")

# CONFIG
SECRET_KEY = os.getenv("SECRET_KEY", "dev_secret_key_change_in_prod")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7 # 7 Days

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            logger.warning("Token decoded but missing 'sub' claim")
            raise credentials_exception
    except JWTError as e:
        logger.warning(f"JWT decode failed: {e}")
        raise credentials_exception
        
    user = await users_collection.find_one({"id": user_id})
    if user is None:
        logger.warning(f"Token valid but user not found in DB", extra={"data": {"user_id": user_id}})
        raise credentials_exception
        
    return UserModel(**user)

from middleware.db_guard import ScopedDatabase
from database import db as raw_db

async def get_db(current_user: UserModel = Depends(get_current_user)) -> ScopedDatabase:
    """Returns a database wrapper that enforces agency_id scoping."""
    return ScopedDatabase(raw_db, current_user.agency_id)
