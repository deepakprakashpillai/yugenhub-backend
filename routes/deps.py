from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from datetime import datetime, timedelta
from typing import Optional, List
from database import users_collection
from models.user import UserModel
from logging_config import get_logger
from config import config

logger = get_logger("auth")

# Config from central config
SECRET_KEY = config.SECRET_KEY
ALGORITHM = config.ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = config.ACCESS_TOKEN_EXPIRE_MINUTES

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


# ─── Centralized RBAC Helpers ────────────────────────────────────────────────

def require_role(*allowed_roles):
    """Dependency that checks if the current user has one of the allowed roles."""
    async def checker(current_user: UserModel = Depends(get_current_user)):
        if current_user.role not in allowed_roles:
            logger.warning(
                f"Access denied: requires {allowed_roles}",
                extra={"data": {"user_id": current_user.id, "role": current_user.role}}
            )
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return current_user
    return checker


async def get_user_verticals(current_user: UserModel, db: ScopedDatabase) -> List[str]:
    """
    Resolve which vertical IDs this user can access.
    - Owner: all verticals (always)
    - Others: user.allowed_verticals if non-empty, else all verticals
    - Returns list of vertical ID strings
    """
    from defaults import DEFAULT_AGENCY_CONFIG
    
    # Fetch agency config for the full verticals list
    agency_config = await db.agency_configs.find_one({})
    all_verticals = [
        v["id"] for v in (agency_config or {}).get("verticals", DEFAULT_AGENCY_CONFIG["verticals"])
    ]
    
    # Owner always gets everything
    if current_user.role == "owner":
        return all_verticals
    
    # Empty allowed_verticals = access to all (backward compatible)
    if not current_user.allowed_verticals:
        return all_verticals
    
    # Return only verticals that actually exist in the config
    return [v for v in current_user.allowed_verticals if v in all_verticals]


def require_finance_access():
    """Dependency that checks if the user has finance access (owner/admin by role, or explicit finance_access flag)."""
    async def checker(current_user: UserModel = Depends(get_current_user)):
        # Owner and admin always have finance access
        if current_user.role in ["owner", "admin"]:
            return current_user
        # Explicit finance_access flag for members
        if current_user.finance_access:
            return current_user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access Denied: Finance data is restricted."
        )
    return checker
