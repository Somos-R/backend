from fastapi import FastAPI

app = FastAPI(
    title="Somos R API",
    description="Backend API for Somos R recycling platform",
    version="0.1.0",
)


@app.get("/health")
def health_check():
    return {"status": "ok"}
