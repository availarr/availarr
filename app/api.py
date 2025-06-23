from fastapi import APIRouter
#from app.webhook import router as webhook_router
from app.config_server import router as config_router
from app.reset import router as reset_router

# Create a master router
api_router = APIRouter()

# Mount feature routers
#api_router.include_router(webhook_router, prefix="/webhook")
api_router.include_router(config_router, prefix="/config")
api_router.include_router(reset_router, prefix="/reset")


# Health check route
@api_router.get("/healthz")
async def health_check():
    return {"status": "ok"}
