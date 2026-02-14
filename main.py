from logging_config import setup_logging

# Initialize logging BEFORE anything else
setup_logging()

from logging_config import get_logger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from middleware import RequestLifecycleMiddleware
from routes import associate, client, config, project, tasks, auth, calendar, notifications, users, dashboard, settings, templates

logger = get_logger("app")

app = FastAPI(title="YugenHub API")

# CORS remains here as it's a global setting
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"], 
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
app.include_router(config.router)
app.include_router(tasks.router)
app.include_router(calendar.router)
app.include_router(notifications.router)
app.include_router(users.router)
app.include_router(dashboard.router)
app.include_router(settings.router)
app.include_router(templates.router)

logger.info("All routers registered, YugenHub API ready")

@app.get("/")
async def root():
    return {"status": "online", "message": "YugenHub API is organized!"}