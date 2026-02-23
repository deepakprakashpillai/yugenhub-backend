from fastapi import APIRouter, Depends, HTTPException, Body, Query
from typing import Optional, List, Dict, Any
# REMOVED raw collection imports
from models.user import UserModel
from models.notification_prefs import NotificationPrefsModel
from routes.deps import get_current_user, get_db
from middleware.db_guard import ScopedDatabase
from defaults import DEFAULT_AGENCY_CONFIG
from logging_config import get_logger
from datetime import datetime
import uuid
import copy

router = APIRouter(prefix="/api/settings", tags=["Settings"])
logger = get_logger("settings")


# ─── RBAC Helpers ────────────────────────────────────────────────────────────

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


def parse_mongo_data(data):
    if isinstance(data, list):
        return [parse_mongo_data(item) for item in data]
    if isinstance(data, dict):
        if "_id" in data:
            data["_id"] = str(data["_id"])
        return {k: parse_mongo_data(v) for k, v in data.items()}
    return data


async def get_or_create_config(db: ScopedDatabase) -> dict:
    """Get agency config, creating with defaults if it doesn't exist."""
    # ScopedDB already filters by agency_id
    config = await db.agency_configs.find_one({})
    if not config:
        # Seed with defaults
        new_config = copy.deepcopy(DEFAULT_AGENCY_CONFIG)
        # agency_id is NOT auto-injected on insert_one IF we just pass dict. 
        # But wait, ScopedCollection.insert_one DOES inject agency_id if not present??
        # Checked middleware: `insert_one` -> `_inject_scope`.
        # So we don't strictly need to add it, but explicit is fine.
        new_config["agency_id"] = db.agency_id
        await db.agency_configs.insert_one(new_config)
        config = await db.agency_configs.find_one({})
        logger.info(f"Seeded default config for new agency", extra={"data": {"agency_id": db.agency_id}})
    return config


# ─── ORGANISATION ────────────────────────────────────────────────────────────

@router.get("/org")
async def get_org(current_user: UserModel = Depends(get_current_user), db: ScopedDatabase = Depends(get_db)):
    """Get organisation details."""
    config = await get_or_create_config(db)
    return parse_mongo_data({
        "org_name": config.get("org_name", "My Agency"),
        "org_email": config.get("org_email", ""),
        "org_phone": config.get("org_phone", ""),
        "agency_id": config.get("agency_id"),
        "theme_mode": config.get("theme_mode", "dark"),
        "accent_color": config.get("accent_color", "#ef4444"),
    })


@router.patch("/org")
async def update_org(
    updates: dict = Body(...),
    current_user: UserModel = Depends(require_role("owner")),
    db: ScopedDatabase = Depends(get_db)
):
    """Update organisation details. Owner only."""
    allowed_fields = {"org_name", "org_email", "org_phone", "theme_mode", "accent_color"}
    filtered = {k: v for k, v in updates.items() if k in allowed_fields}

    if not filtered:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    await db.agency_configs.update_one(
        {}, # Scope injected
        {"$set": filtered}
    )
    logger.info(f"Org updated", extra={"data": {"agency_id": current_user.agency_id, "fields": list(filtered.keys())}})
    return {"message": "Organisation updated", "updated": list(filtered.keys())}


# ─── TEAM MANAGEMENT ────────────────────────────────────────────────────────

@router.get("/team")
async def get_team(current_user: UserModel = Depends(get_current_user), db: ScopedDatabase = Depends(get_db)):
    """List all team members for this agency."""
    users = await db.users.find({}).to_list(1000)

    return parse_mongo_data([
        {
            "id": u.get("id"),
            "name": u.get("name", "Unknown"),
            "email": u.get("email"),
            "role": u.get("role", "member"),
            "picture": u.get("picture"),
            "phone": u.get("phone"),
            "status": u.get("status", "active"),
            "last_login": u.get("last_login"),
            "created_at": u.get("created_at"),
        }
        for u in users
    ])


async def sync_user_to_associate(db: ScopedDatabase, user_data: dict, associate_role: str = "Lead"):
    """Auto-create an In-house associate record for a new team member."""
    email = user_data.get("email")
    if not email:
        return
    # Check if an associate with this email already exists
    existing = await db.associates.find_one({"email_id": email})
    if existing:
        return
    associate = {
        "name": user_data.get("name", "Unknown"),
        "phone_number": user_data.get("phone", ""),
        "email_id": email,
        "primary_role": associate_role,
        "employment_type": "In-house",
        "is_active": True,
        "linked_user_id": user_data.get("id"),
        "agency_id": user_data.get("agency_id"),
        "created_at": datetime.now(),
    }
    await db.associates.insert_one(associate)
    logger.info(f"Auto-created associate for invited user", extra={"data": {"email": email}})


@router.post("/team/invite")
async def invite_user(
    invite_data: dict = Body(...),
    current_user: UserModel = Depends(require_role("owner", "admin")),
    db: ScopedDatabase = Depends(get_db)
):
    """Invite a user by email. Creates a pending user record and an In-house associate."""
    email = invite_data.get("email", "").strip().lower()
    role = invite_data.get("role", "member")
    associate_role = invite_data.get("associate_role", "Lead")

    if not email:
        raise HTTPException(status_code=400, detail="Email is required")

    if role not in ("member", "admin"):
        raise HTTPException(status_code=400, detail="Invalid role. Must be 'member' or 'admin'")

    # Admin cannot invite with owner role
    if current_user.role == "admin" and role == "owner":
        raise HTTPException(status_code=403, detail="Admins cannot assign owner role")

    # Check if user already exists in this agency
    existing = await db.users.find_one({
        "email": email
    })
    if existing:
        raise HTTPException(status_code=409, detail="User already exists in this agency")

    # Create pending user record
    new_user = {
        "id": str(uuid.uuid4()),
        "google_id": "",
        "email": email,
        "name": email.split("@")[0].title(),
        "picture": None,
        "agency_id": current_user.agency_id,
        "role": role,
        "status": "pending",
        "created_at": datetime.now(),
        "last_login": None,
        "invited_by": current_user.id,
    }

    await db.users.insert_one(new_user)

    # Auto-create In-house associate
    await sync_user_to_associate(db, new_user, associate_role)

    logger.info(
        f"User invited",
        extra={"data": {"email": email, "role": role, "associate_role": associate_role, "invited_by": current_user.email, "agency_id": current_user.agency_id}}
    )
    return {"message": f"Invitation sent to {email}", "user_id": new_user["id"]}


@router.patch("/team/{user_id}/role")
async def change_user_role(
    user_id: str,
    role_data: dict = Body(...),
    current_user: UserModel = Depends(require_role("owner", "admin")),
    db: ScopedDatabase = Depends(get_db)
):
    """Change a user's role."""
    new_role = role_data.get("role")
    if new_role not in ("member", "admin", "owner"):
        raise HTTPException(status_code=400, detail="Invalid role")

    # Find target user
    target = await db.users.find_one({
        "id": user_id
    })
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Prevent self-demotion for owners
    if target["id"] == current_user.id and current_user.role == "owner" and new_role != "owner":
        raise HTTPException(status_code=400, detail="Cannot change your own owner role")

    # Admin cannot promote to owner or demote other admins
    if current_user.role == "admin":
        if new_role == "owner":
            raise HTTPException(status_code=403, detail="Admins cannot assign owner role")
        if target.get("role") == "admin" and target["id"] != current_user.id:
            raise HTTPException(status_code=403, detail="Admins cannot change other admin roles")

    await db.users.update_one(
        {"id": user_id},
        {"$set": {"role": new_role}}
    )
    logger.info(
        f"Role changed",
        extra={"data": {"target_user": user_id, "new_role": new_role, "changed_by": current_user.id}}
    )
    return {"message": f"Role updated to {new_role}"}


@router.patch("/team/{user_id}")
async def update_user_details(
    user_id: str,
    updates: dict = Body(...),
    current_user: UserModel = Depends(require_role("owner", "admin")),
    db: ScopedDatabase = Depends(get_db)
):
    """Update user details (Name, Email, Phone)."""
    allowed_fields = {"name", "email", "phone"}
    filtered = {k: v for k, v in updates.items() if k in allowed_fields}

    if not filtered:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    # Find target user
    target = await db.users.find_one({"id": user_id})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Permission Checks
    # 1. Admins cannot edit Owners
    if current_user.role == "admin" and target.get("role") == "owner":
         raise HTTPException(status_code=403, detail="Admins cannot edit Owners")
    
    # 2. Admins can only edit Members (and themselves)
    if current_user.role == "admin" and target.get("role") == "admin" and target["id"] != current_user.id:
         raise HTTPException(status_code=403, detail="Admins cannot edit other Admins")

    # Email Validation/Uniqueness (if email is being changed)
    if "email" in filtered:
        new_email = filtered["email"].strip().lower()
        if new_email != target["email"]:
            existing = await db.users.find_one({"email": new_email})
            if existing:
                raise HTTPException(status_code=409, detail="Email already in use")
            filtered["email"] = new_email

    await db.users.update_one(
        {"id": user_id},
        {"$set": filtered}
    )
    
    logger.info(
        f"User details updated",
        extra={"data": {"target": user_id, "updated_by": current_user.id, "fields": list(filtered.keys())}}
    )
    return {"message": "User details updated"}


@router.delete("/team/{user_id}")
async def remove_user(
    user_id: str,
    deactivate_associate: bool = Query(False, description="Also deactivate linked associate"),
    current_user: UserModel = Depends(require_role("owner", "admin")),
    db: ScopedDatabase = Depends(get_db)
):
    """Remove a user from the agency. Optionally deactivate linked associate."""
    target = await db.users.find_one({
        "id": user_id
    })
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Cannot remove yourself
    if target["id"] == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot remove yourself")

    # Cannot remove owner
    if target.get("role") == "owner":
        raise HTTPException(status_code=403, detail="Cannot remove the owner")

    # Admin cannot remove other admins
    if current_user.role == "admin" and target.get("role") == "admin":
        raise HTTPException(status_code=403, detail="Admins cannot remove other admins")

    # Optionally deactivate linked associate
    if deactivate_associate and target.get("email"):
        await db.associates.update_one(
            {"email_id": target["email"]},
            {"$set": {"is_active": False}}
        )
        logger.info(f"Deactivated linked associate", extra={"data": {"email": target["email"]}})

    await db.users.delete_one({
        "id": user_id
    })
    logger.info(
        f"User removed",
        extra={"data": {"removed_user": user_id, "removed_by": current_user.id, "agency_id": current_user.agency_id}}
    )
    return {"message": "User removed"}


# ─── WORKFLOW CONFIG ─────────────────────────────────────────────────────────

# The 5 fixed status IDs that cannot be modified or deleted
FIXED_STATUS_IDS = {"enquiry", "booked", "ongoing", "completed", "cancelled"}


@router.get("/workflow")
async def get_workflow(current_user: UserModel = Depends(get_current_user), db: ScopedDatabase = Depends(get_db)):
    """Get workflow configuration (statuses, lead sources, deliverable types)."""
    config = await get_or_create_config(db)

    # Ensure fixed flag is present on status options (backfill for existing agencies)
    status_options = config.get("status_options", DEFAULT_AGENCY_CONFIG["status_options"])
    for opt in status_options:
        if opt.get("id") in FIXED_STATUS_IDS:
            opt["fixed"] = True
        elif "fixed" not in opt:
            opt["fixed"] = False

    # Helper function to rescue flat string arrays that were accidentally seeded as dicts
    def coerce_to_strings(array_data):
        if not array_data:
            return []
        coerced = []
        for item in array_data:
            if isinstance(item, dict):
                # Attempt to extract 'label' or 'name' or 'id', defaulting to str(item)
                val = item.get("label") or item.get("name") or item.get("id") or str(item)
                coerced.append(val)
            else:
                coerced.append(str(item))
        return coerced

    return parse_mongo_data({
        "status_options": status_options,
        "lead_sources": coerce_to_strings(config.get("lead_sources", DEFAULT_AGENCY_CONFIG["lead_sources"])),
        "deliverable_types": coerce_to_strings(config.get("deliverable_types", DEFAULT_AGENCY_CONFIG["deliverable_types"])),
        "associate_roles": coerce_to_strings(config.get("associate_roles", DEFAULT_AGENCY_CONFIG.get("associate_roles", []))),
    })


@router.patch("/workflow")
async def update_workflow(
    updates: dict = Body(...),
    current_user: UserModel = Depends(require_role("owner")),
    db: ScopedDatabase = Depends(get_db)
):
    """Update workflow configuration. Owner only.
    Protects fixed statuses from modification or removal.
    """
    allowed_fields = {"status_options", "lead_sources", "deliverable_types", "associate_roles"}
    filtered = {k: v for k, v in updates.items() if k in allowed_fields}

    if not filtered:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    # If status_options is being updated, enforce fixed status protection
    if "status_options" in filtered:
        new_options = filtered["status_options"]

        # Get current config to retrieve the original fixed statuses
        config = await get_or_create_config(db)
        current_options = config.get("status_options", DEFAULT_AGENCY_CONFIG["status_options"])

        # Build map of current fixed statuses (by id)
        fixed_originals = {
            opt["id"]: opt for opt in current_options if opt.get("id") in FIXED_STATUS_IDS
        }

        # Ensure all 5 fixed statuses are present and unchanged
        new_ids = {opt.get("id") for opt in new_options}
        for fid, fopt in fixed_originals.items():
            if fid not in new_ids:
                # Re-add missing fixed status
                new_options.append(fopt)
            else:
                # Overwrite any modifications to fixed statuses with original values
                for i, opt in enumerate(new_options):
                    if opt.get("id") == fid:
                        new_options[i] = {**fopt, "fixed": True}
                        break

        # Mark custom statuses
        for opt in new_options:
            if opt.get("id") not in FIXED_STATUS_IDS:
                opt["fixed"] = False
            else:
                opt["fixed"] = True

        filtered["status_options"] = new_options

    await db.agency_configs.update_one(
        {},
        {"$set": filtered}
    )
    logger.info(
        f"Workflow config updated",
        extra={"data": {"agency_id": current_user.agency_id, "fields": list(filtered.keys())}}
    )
    return {"message": "Workflow config updated", "updated": list(filtered.keys())}


@router.get("/workflow/status/{status_id}/usage")
async def get_status_usage(
    status_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """Get the number of projects using a specific status."""
    count = await db.projects.count_documents({
        "status": status_id
    })
    return {"status_id": status_id, "count": count}


@router.post("/workflow/status/delete")
async def delete_status(
    body: dict = Body(...),
    current_user: UserModel = Depends(require_role("owner")),
    db: ScopedDatabase = Depends(get_db)
):
    """Delete a custom status and reassign all projects using it.
    Body: { "delete_id": "custom_1", "reassign_to": "enquiry" }
    """
    delete_id = body.get("delete_id")
    reassign_to = body.get("reassign_to")

    if not delete_id or not reassign_to:
        raise HTTPException(status_code=400, detail="Both 'delete_id' and 'reassign_to' are required")

    if delete_id in FIXED_STATUS_IDS:
        raise HTTPException(status_code=400, detail="Cannot delete a fixed status")

    if delete_id == reassign_to:
        raise HTTPException(status_code=400, detail="Cannot reassign to the same status being deleted")

    # Verify reassign_to exists in config
    config = await get_or_create_config(db)
    status_options = config.get("status_options", [])
    reassign_exists = any(opt.get("id") == reassign_to for opt in status_options)
    if not reassign_exists:
        raise HTTPException(status_code=400, detail=f"Reassignment status '{reassign_to}' does not exist")

    delete_exists = any(opt.get("id") == delete_id for opt in status_options)
    if not delete_exists:
        raise HTTPException(status_code=404, detail=f"Status '{delete_id}' not found")

    # 1. Reassign all projects with the deleted status
    result = await db.projects.update_many(
        {"status": delete_id},
        {"$set": {"status": reassign_to, "updated_on": datetime.now()}}
    )
    reassigned_count = result.modified_count

    # 2. Remove the status from config
    new_options = [opt for opt in status_options if opt.get("id") != delete_id]
    await db.agency_configs.update_one(
        {},
        {"$set": {"status_options": new_options}}
    )

    # Get labels for message
    delete_label = next((opt.get("label", delete_id) for opt in status_options if opt.get("id") == delete_id), delete_id)
    reassign_label = next((opt.get("label", reassign_to) for opt in status_options if opt.get("id") == reassign_to), reassign_to)

    logger.info(
        f"Status deleted",
        extra={"data": {
            "agency_id": current_user.agency_id,
            "deleted_status": delete_id,
            "reassigned_to": reassign_to,
            "reassigned_count": reassigned_count,
        }}
    )
    return {
        "message": f"Status '{delete_label}' deleted, {reassigned_count} projects reassigned to '{reassign_label}'",
        "reassigned_count": reassigned_count
    }


# ─── VERTICALS ───────────────────────────────────────────────────────────────

@router.get("/verticals")
async def get_verticals(current_user: UserModel = Depends(get_current_user), db: ScopedDatabase = Depends(get_db)):
    """Get verticals configuration."""
    config = await get_or_create_config(db)
    return parse_mongo_data({
        "verticals": config.get("verticals", DEFAULT_AGENCY_CONFIG["verticals"]),
    })


@router.patch("/verticals")
async def update_verticals(
    updates: dict = Body(...),
    current_user: UserModel = Depends(require_role("owner")),
    db: ScopedDatabase = Depends(get_db)
):
    """Update verticals configuration. Owner only."""
    verticals = updates.get("verticals")
    if verticals is None:
        raise HTTPException(status_code=400, detail="'verticals' field required")

    await db.agency_configs.update_one(
        {},
        {"$set": {"verticals": verticals}}
    )
    logger.info(
        f"Verticals config updated",
        extra={"data": {"agency_id": current_user.agency_id, "count": len(verticals)}}
    )
    return {"message": "Verticals updated"}


# ─── FINANCE SETTINGS ────────────────────────────────────────────────────────

@router.get("/finance/categories")
async def get_finance_categories(
    current_user: UserModel = Depends(get_current_user), 
    db: ScopedDatabase = Depends(get_db)
):
    """Get finance categories configuration."""
    config = await get_or_create_config(db)
    return parse_mongo_data({
        "categories": config.get("finance_categories", DEFAULT_AGENCY_CONFIG["finance_categories"])
    })


@router.patch("/finance/categories")
async def update_finance_categories(
    updates: dict = Body(...),
    current_user: UserModel = Depends(require_role("owner", "admin")),
    db: ScopedDatabase = Depends(get_db)
):
    """Update finance categories configuration."""
    categories = updates.get("categories")
    if categories is None:
        raise HTTPException(status_code=400, detail="'categories' field required")

    await db.agency_configs.update_one(
        {},
        {"$set": {"finance_categories": categories}}
    )
    logger.info(
        f"Finance categories updated",
        extra={"data": {"agency_id": current_user.agency_id, "count": len(categories)}}
    )
    return {"message": "Finance categories updated"}


# ─── NOTIFICATION PREFERENCES ───────────────────────────────────────────────

NOTIFICATION_PREFS_COLLECTION = "notification_prefs"

@router.get("/notifications")
async def get_notification_prefs(current_user: UserModel = Depends(get_current_user), db: ScopedDatabase = Depends(get_db)):
    """Get current user's notification preferences."""
    # NOTIFICATION_PREFS_COLLECTION = "notification_prefs" usually part of same DB
    # We can use db.get_collection for custom name if not in wrapper properties
    # OR we can add notification_prefs to wrapper if used often.
    # For now, generic get_collection is fine.
    
    prefs_collection = db.get_collection(NOTIFICATION_PREFS_COLLECTION)

    prefs = await prefs_collection.find_one({
        "user_id": current_user.id
    })

    if not prefs:
        # Return defaults
        return {
            "task_assigned": True,
            "task_updated": True,
            "project_created": True,
            "project_completed": True,
            "mentions": True,
            "email_notifications": False,
        }

    return parse_mongo_data({
        "task_assigned": prefs.get("task_assigned", True),
        "task_updated": prefs.get("task_updated", True),
        "project_created": prefs.get("project_created", True),
        "project_completed": prefs.get("project_completed", True),
        "mentions": prefs.get("mentions", True),
        "email_notifications": prefs.get("email_notifications", False),
    })


@router.patch("/notifications")
async def update_notification_prefs(
    updates: dict = Body(...),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """Update current user's notification preferences."""
    prefs_collection = db.get_collection(NOTIFICATION_PREFS_COLLECTION)

    allowed_fields = {
        "task_assigned", "task_updated", "project_created",
        "project_completed", "mentions", "email_notifications"
    }
    filtered = {k: v for k, v in updates.items() if k in allowed_fields and isinstance(v, bool)}

    if not filtered:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    await prefs_collection.update_one(
        {"user_id": current_user.id},
        {"$set": filtered},
        upsert=True
    )
    logger.info(
        f"Notification prefs updated",
        extra={"data": {"user_id": current_user.id, "fields": list(filtered.keys())}}
    )
    return {"message": "Notification preferences updated"}


# ─── ACCOUNT ─────────────────────────────────────────────────────────────────

@router.get("/account")
async def get_account(current_user: UserModel = Depends(get_current_user)):
    """Get current user's account info."""
    return {
        "id": current_user.id,
        "name": current_user.name,
        "email": current_user.email,
        "role": current_user.role,
        "agency_id": current_user.agency_id,
        "picture": current_user.picture,
        "phone": current_user.phone,
    }


@router.patch("/account")
async def update_account(
    updates: dict = Body(...),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """Update current user's own profile (name, phone)."""
    allowed_fields = {"name", "phone"}
    filtered = {k: v for k, v in updates.items() if k in allowed_fields}

    if not filtered:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    # Validate name is not empty
    if "name" in filtered and not filtered["name"].strip():
        raise HTTPException(status_code=400, detail="Name cannot be empty")

    await db.users.update_one({"id": current_user.id}, {"$set": filtered})
    logger.info(
        f"Account updated",
        extra={"data": {"user_id": current_user.id, "fields": list(filtered.keys())}}
    )
    return {"message": "Profile updated"}


# ─── DANGER ZONE ─────────────────────────────────────────────────────────────

@router.post("/reset-config")
async def reset_config(
    current_user: UserModel = Depends(require_role("owner")),
    db: ScopedDatabase = Depends(get_db)
):
    """Reset agency config to defaults. Owner only."""
    new_config = copy.deepcopy(DEFAULT_AGENCY_CONFIG)
    new_config["agency_id"] = current_user.agency_id

    await db.agency_configs.update_one(
        {},
        {"$set": new_config},
        upsert=True
    )
    logger.info(
        f"Config reset to defaults",
        extra={"data": {"agency_id": current_user.agency_id, "reset_by": current_user.id}}
    )
    return {"message": "Configuration reset to defaults"}
