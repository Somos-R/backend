from fastapi import FastAPI

from app.domains.auth.router import router as auth_router
from app.domains.catalogs.router import router as catalogs_router

app = FastAPI(
    title="Somos R API",
    description="Backend API for Somos R recycling platform",
    version="0.1.0",
)

app.include_router(auth_router)
app.include_router(catalogs_router)


@app.get("/health")
def health_check():
    return {"status": "ok"}
