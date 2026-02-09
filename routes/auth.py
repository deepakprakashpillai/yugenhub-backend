from fastapi import APIRouter, Body, HTTPException, Depends
from google.oauth2 import id_token
from google.auth.transport import requests
from database import users_collection, configs_collection
from models.user import UserModel
from routes.deps import create_access_token
from datetime import datetime
import os

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

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
        raise HTTPException(status_code=400, detail="Missing Google Token")

    try:
        # 1. Verify Google Token
        # Use verify_oauth2_token to strictly validate the integrity and source of the token
        try:
             id_info = id_token.verify_oauth2_token(token, requests.Request(), GOOGLE_CLIENT_ID)
        except ValueError as e:
             print(f"Token verification failed: {e}")
             raise HTTPException(status_code=401, detail="Invalid Google Token")

        email = id_info.get("email")
        google_id = id_info.get("sub")
        name = id_info.get("name")
        picture = id_info.get("picture")

        if not email:
            raise HTTPException(status_code=400, detail="Invalid Token: No email found")

        # 2. Check Database for Existing User
        user_doc = await users_collection.find_one({"email": email})

        if not user_doc:
            # INVITE ONLY MODEL -> REJECT
            raise HTTPException(status_code=403, detail="Access Denied. Your email is not whitelisted.")
        
        # 3. Update User Info (Sync latest name/pic from Google)
        current_user = UserModel(**user_doc)
        
        # Only update if changed
        update_data = {"last_login": datetime.now()}
        if not current_user.google_id:
            update_data["google_id"] = google_id # Link account if not linked
        if picture and current_user.picture != picture:
            update_data["picture"] = picture
            
        await users_collection.update_one({"_id": user_doc["_id"]}, {"$set": update_data})
        
        # 4. Issue Internal JWT
        access_token = create_access_token(
            data={"sub": current_user.id, "agency_id": current_user.agency_id}
        )

        return {
            "access_token": access_token, 
            "token_type": "bearer",
            "user": {
                "id": current_user.id,
                "name": current_user.name,
                "email": current_user.email,
                "role": current_user.role,
                "agency_id": current_user.agency_id,
                "picture": current_user.picture
            }
        }

    except Exception as e:
        print(f"Auth Error: {e}")
        raise HTTPException(status_code=400, detail=f"Authentication failed: {str(e)}")


# ============ DEV-ONLY ENDPOINTS ============
# WARNING: Remove or disable these in production!

@router.get("/dev/users")
async def list_dev_users():
    """[DEV ONLY] List all users for dev login selector."""
    users = await users_collection.find({}).to_list(100)
    return [
        {
            "id": u["id"],
            "name": u.get("name", "Unknown"),
            "email": u.get("email"),
            "role": u.get("role", "member"),
            "picture": u.get("picture"),
        }
        for u in users
    ]


@router.post("/dev/login/{user_id}")
async def dev_login(user_id: str):
    """[DEV ONLY] Issue a JWT for any user, bypassing Google OAuth."""
    user_doc = await users_collection.find_one({"id": user_id})
    if not user_doc:
        raise HTTPException(status_code=404, detail="User not found")

    user = UserModel(**user_doc)
    access_token = create_access_token(
        data={"sub": user.id, "agency_id": user.agency_id}
    )

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
        },
    }

@router.get("/dev/seed")
async def seed_dev_users_endpoint():
    """[DEV ONLY] Seed test users."""
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
            
    return {"message": f"Seeded {count} users"}
