from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.domains.auth.router import router as auth_router
from app.domains.catalogs.router import router as catalogs_router
from app.domains.inventory.router import router as inventory_router
from app.domains.transactions.router import router as transactions_router
from app.domains.users.router import router as users_router
from app.domains.weighings.router import router as weighings_router

app = FastAPI(
    title="Somos R API",
    description="Backend API for Somos R recycling platform",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "https://somosr.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(catalogs_router)
app.include_router(users_router)
app.include_router(inventory_router)
app.include_router(weighings_router)
app.include_router(transactions_router)


@app.get("/health")
def health_check():
    return {"status": "ok"}
