from contextlib import asynccontextmanager
from asgi_correlation_id.middleware import CorrelationIdMiddleware
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from settings import settings
from logger import logger
from external.base import external_services
from server.routers import health, session, artifact, run, agent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan manager for FastAPI application."""
    # Startup
    logger.info("Starting up application...")

    # Initialize all external services
    services = external_services()
    for service in services:
        name = type(service).__name__
        logger.info(f"Initializing {name}...")
        await service.startup()
        await service.sanity_check()
        logger.info(f"{name} initialized successfully")

    logger.info("All services initialized successfully")

    yield

    # Shutdown
    logger.info("Shutting down application...")
    for service in services:
        name = type(service).__name__
        logger.info(f"Shutting down {name}...")
        await service.shutdown()
        logger.info(f"{name} shut down successfully")


app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=3600,
)
app.add_middleware(CorrelationIdMiddleware)

# Include routers
app.include_router(health.router)
app.include_router(session.router)
app.include_router(artifact.router)
app.include_router(run.router)
app.include_router(agent.router)


if __name__ == "__main__":
    uvicorn.run(
        "server.main:app",
        host=settings.BACKEND_HOST,
        port=settings.BACKEND_PORT,
        reload=True,
    )
