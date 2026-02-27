from fastapi import APIRouter, Body, HTTPException, Depends
from google.oauth2 import id_token
from google.auth.transport import requests
from database import users_collection 
from models.user import UserModel
from routes.deps import create_access_token
from datetime import datetime
from logging_config import get_logger
import os
from config import config

router = APIRouter(prefix="/api/auth", tags=["Authentication"])
logger = get_logger("auth")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID") 

@router.post("/google")
async def google_login(token_data: dict = Body(...)):
    """
    Verifies Google ID Token.
    - If user exists -> Login (Success)
    - If user DOES NOT exist -> REJECT (Invite Only)
    """
    token = token_data.get("token")
    if not token:
        logger.warning("Login attempt with missing Google token")
        raise HTTPException(status_code=400, detail="Missing Google Token")

    try:
        # 1. Verify Google Token
        try:
             id_info = id_token.verify_oauth2_token(token, requests.Request(), GOOGLE_CLIENT_ID)
        except ValueError as e:
             logger.warning(f"Google token verification failed: {e}")
             raise HTTPException(status_code=401, detail="Invalid Google Token")

        email = id_info.get("email")
        google_id = id_info.get("sub")
        name = id_info.get("name")
        picture = id_info.get("picture")

        if not email:
            logger.warning("Google token decoded but no email found")
            raise HTTPException(status_code=400, detail="Invalid Token: No email found")

        # 2. Check Database for Existing User
        user_doc = await users_collection.find_one({"email": email})

        if not user_doc:
            # INVITE ONLY MODEL -> REJECT
            logger.warning(f"Login denied: email not whitelisted", extra={"data": {"email": email}})
            raise HTTPException(status_code=403, detail="Access Denied. Your email is not whitelisted.")
        
        # 3. Update User Info (Sync latest name/pic from Google)
        current_user = UserModel(**user_doc)
        
        # Only update if changed
        update_data = {"last_login": datetime.now(), "status": "active"}
        if not current_user.google_id:
            update_data["google_id"] = google_id # Link account if not linked
        if picture and current_user.picture != picture:
            update_data["picture"] = picture
        # Sync name from Google if it was auto-generated from email during invite
        if name and (not current_user.name or current_user.name == current_user.email.split("@")[0].title()):
            update_data["name"] = name
            
        await users_collection.update_one({"_id": user_doc["_id"]}, {"$set": update_data})
        
        # Use updated values for the response
        response_name = update_data.get("name", current_user.name)
        response_picture = update_data.get("picture", current_user.picture)
        
        # 4. Issue Internal JWT
        access_token = create_access_token(
            data={"sub": current_user.id, "agency_id": current_user.agency_id}
        )

        logger.info(
            f"Login successful",
            extra={"data": {"email": email, "user_id": current_user.id, "role": current_user.role, "agency_id": current_user.agency_id}}
        )

        return {
            "access_token": access_token, 
            "token_type": "bearer",
            "user": {
                "id": current_user.id,
                "name": response_name,
                "email": current_user.email,
                "role": current_user.role,
                "agency_id": current_user.agency_id,
                "picture": response_picture,
                "finance_access": current_user.finance_access,
                "can_manage_team": current_user.can_manage_team,
            }
        }

    except HTTPException:
        raise  # Re-raise HTTP exceptions as-is
    except Exception as e:
        logger.error(f"Authentication failed with unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=f"Authentication failed: {str(e)}")


# ============ DEV-ONLY ENDPOINTS ============
# WARNING: Remove or disable these in production!

@router.get("/dev/users")
async def list_dev_users():
    """[DEV ONLY] List all users for dev login selector."""
    if config.ENV == "production":
        raise HTTPException(status_code=404, detail="Not Found")
    logger.debug("Dev endpoint: listing users")
    users = await users_collection.find({}).to_list(100)
    return [
        {
            "id": u["id"],
            "name": u.get("name", "Unknown"),
            "email": u.get("email"),
            "role": u.get("role", "member"),
            "picture": u.get("picture"),
            "finance_access": u.get("finance_access", False),
            "can_manage_team": u.get("can_manage_team", False),
        }
        for u in users
    ]


@router.post("/dev/login/{user_id}")
async def dev_login(user_id: str):
    """[DEV ONLY] Issue a JWT for any user, bypassing Google OAuth."""
    if config.ENV == "production":
        raise HTTPException(status_code=404, detail="Not Found")
    user_doc = await users_collection.find_one({"id": user_id})
    if not user_doc:
        logger.warning(f"Dev login failed: user not found", extra={"data": {"user_id": user_id}})
        raise HTTPException(status_code=404, detail="User not found")

    user = UserModel(**user_doc)
    access_token = create_access_token(
        data={"sub": user.id, "agency_id": user.agency_id}
    )

    logger.info(f"Dev login successful", extra={"data": {"user_id": user.id, "email": user.email, "agency_id": user.agency_id}})

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "role": user.role,
            "agency_id": user.agency_id,
            "picture": user.picture,
            "finance_access": user.finance_access,
            "can_manage_team": user.can_manage_team,
        },
    }

@router.get("/dev/seed")
async def seed_dev_users_endpoint():
    """[DEV ONLY] Seed test users."""
    if config.ENV == "production":
        raise HTTPException(status_code=404, detail="Not Found")
    import uuid
    from models.user import UserModel
    
    test_users = [
        {"name": "Project Manager (Test)", "email": "pm@test.com", "role": "admin", "picture": "https://api.dicebear.com/7.x/avataaars/svg?seed=pm", "google_id": f"test_{uuid.uuid4()}", "agency_id": "default_agency"},
        {"name": "Senior Editor (Test)", "email": "editor@test.com", "role": "member", "picture": "https://api.dicebear.com/7.x/avataaars/svg?seed=editor", "google_id": f"test_{uuid.uuid4()}", "agency_id": "default_agency"},
        {"name": "Client Service (Test)", "email": "cs@test.com", "role": "member", "picture": "https://api.dicebear.com/7.x/avataaars/svg?seed=cs", "google_id": f"test_{uuid.uuid4()}", "agency_id": "default_agency"},
        {"name": "Super Admin (Test)", "email": "admin@test.com", "role": "owner", "picture": "https://api.dicebear.com/7.x/avataaars/svg?seed=admin", "google_id": f"test_{uuid.uuid4()}", "agency_id": "default_agency"}
    ]
    
    count = 0
    for u in test_users:
        if not await users_collection.find_one({"email": u["email"]}):
            await users_collection.insert_one(UserModel(**u).model_dump(by_alias=True))
            count += 1
    
    logger.info(f"Dev seed completed", extra={"data": {"users_created": count}})
    return {"message": f"Seeded {count} users"}
