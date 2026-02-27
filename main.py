from logging_config import setup_logging

# Initialize logging BEFORE anything else
setup_logging()

from logging_config import get_logger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from middleware import RequestLifecycleMiddleware
from routes import associate, client, config as config_router, project, tasks, auth, calendar, notifications, users, dashboard, settings, templates, finance, push
from config import config

logger = get_logger("app")

app = FastAPI(title="YugenHub API")

# CORS remains here as it's a global setting
app.add_middleware(
    CORSMiddleware,
    allow_origins=[config.FRONTEND_URL] if config.ENV == "production" else ["*"], 
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

logger.info("All routers registered, YugenHub API ready")

@app.get("/")
async def root():
    return {"status": "online", "message": "YugenHub API is organized!"}