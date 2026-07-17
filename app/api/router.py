from fastapi import APIRouter

from app.api.routers import conversations, health, knowledge, papers


router = APIRouter()
router.include_router(health.router)
router.include_router(papers.router)
router.include_router(knowledge.router)
router.include_router(conversations.router)
