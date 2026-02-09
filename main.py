from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routes import associate, client, config, project, tasks, auth, calendar, notifications, users, dashboard

app = FastAPI(title="YugenHub API")

# CORS remains here as it's a global setting
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

@app.get("/")
async def root():
    return {"status": "online", "message": "YugenHub API is organized!"}