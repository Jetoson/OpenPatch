from fastapi import FastAPI
from api.routers import agent
from database import engine, Base

# Create the database tables on startup
Base.metadata.create_all(bind=engine)

app = FastAPI(title="OpenPatch Orchestrator")

app.include_router(agent.router)

@app.get("/")
def health_check():
    return {"status": "Server is running"}