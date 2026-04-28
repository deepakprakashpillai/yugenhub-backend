from logging_config import setup_logging

# Initialize logging BEFORE anything else
setup_logging()

import asyncio
from contextlib import asynccontextmanager
from logging_config import get_logger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from middleware import RequestLifecycleMiddleware
from routes import associate, client, config as config_router, project, tasks, auth, calendar, notifications, users, dashboard, settings, templates, finance, push, integration, agent, portal, album, media as media_router_module, maps as maps_router_module, communications as communications_router_module, editor as editor_router_module
from config import config

logger = get_logger("app")


@asynccontextmanager
async def lifespan(app):
    from routes.album import expire_albums_loop
    from database import db as _db
    from services.communication_scheduler import start_scheduler, stop_scheduler

    # Create indexes for media library collections
    await _db.media_folders.create_index([("agency_id", 1), ("parent_id", 1)])
    await _db.media_folders.create_index([("agency_id", 1), ("path", 1)])
    await _db.media_items.create_index([("agency_id", 1), ("folder_id", 1)])
    await _db.media_items.create_index([("agency_id", 1), ("status", 1)])
    await _db.media_items.create_index([("share_token", 1)], sparse=True)
    await _db.media_folders.create_index([("share_token", 1)], sparse=True)
    await _db.media_items.create_index([("source_deliverable_id", 1)], sparse=True)
    await _db.bucket_stats_cache.create_index([("agency_id", 1)], unique=True)
    await _db.migration_jobs.create_index([("agency_id", 1), ("started_at", -1)])

    # Create indexes for communications collections
    await _db.communications_messages.create_index([("agency_id", 1), ("status", 1), ("created_at", -1)])
    await _db.communications_messages.create_index([("agency_id", 1), ("recipient_id", 1)])
    await _db.communications_messages.create_index([("agency_id", 1), ("alert_type", 1)])
    await _db.communication_settings.create_index([("agency_id", 1)], unique=True)

    task = asyncio.create_task(expire_albums_loop())
    start_scheduler()
    yield
    task.cancel()
    stop_scheduler()


app = FastAPI(title="YugenHub API", lifespan=lifespan)

# CORS remains here as it's a global setting
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.FRONTEND_URL if config.ENV == "production" else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request lifecycle middleware (request ID, context vars, duration logging)
app.add_middleware(RequestLifecycleMiddleware)

# REGISTER ROUTERS
app.include_router(auth.router)
app.include_router(project.router)
app.include_router(client.router)
app.include_router(associate.router)
app.include_router(config_router.router)
app.include_router(tasks.router)
app.include_router(calendar.router)
app.include_router(notifications.router)
app.include_router(users.router)
app.include_router(dashboard.router)
app.include_router(settings.router)
app.include_router(templates.router)
app.include_router(finance.router)
app.include_router(push.router)
app.include_router(integration.router)
app.include_router(agent.router)
app.include_router(portal.router)
app.include_router(album.router)
app.include_router(media_router_module.router)
app.include_router(maps_router_module.router)
app.include_router(communications_router_module.router)
app.include_router(editor_router_module.router)

logger.info("All routers registered, YugenHub API ready")

@app.get("/")
async def root():
    return {"status": "online", "message": "YugenHub API is organized!"}