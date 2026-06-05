from fastapi import APIRouter
from app.api.v1.routers import admin, auth, fill, forms, health, upload
from app.api.v1.routers.companies import router as companies_router, doc_router as documents_router

api_router = APIRouter()
api_router.include_router(admin.router)
api_router.include_router(auth.router)
api_router.include_router(fill.router)
api_router.include_router(forms.router)
api_router.include_router(health.router)
api_router.include_router(upload.router)
api_router.include_router(companies_router)
api_router.include_router(documents_router)
