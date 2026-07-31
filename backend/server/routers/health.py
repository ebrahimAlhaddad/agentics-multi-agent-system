from fastapi import APIRouter

router = APIRouter(tags=["health"])

@router.get("/")
async def health_check():
    """Health check endpoint."""
    return {"status": "Server is running"}

@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "Server is running"}