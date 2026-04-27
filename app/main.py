import os
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.database import engine, Base
from app.routers import medications

app = FastAPI(
    title="MediTrack Medical Service",
    description="Medication tracking and adherence management",
    version="1.0.0",
)

# CORS — read allowed origins from env (comma-separated), default to localhost for dev
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173,http://localhost").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


# Security headers middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# Create tables on startup
import time
import logging

@app.on_event("startup")
async def startup():
    max_retries = 10
    retry_delay = 3
    
    for attempt in range(max_retries):
        try:
            Base.metadata.create_all(bind=engine)
            logging.info("Database connected successfully!")
            break
        except Exception as e:
            if attempt == max_retries - 1:
                logging.error("Database connection failed after maximum retries.")
                raise
            logging.warning(f"Database connection attempt {attempt+1} failed: {e}. Retrying in {retry_delay}s...")
            time.sleep(retry_delay)


# Routers
app.include_router(medications.router)


# Health check
@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "service": "medical-service",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
