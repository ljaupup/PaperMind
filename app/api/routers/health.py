from fastapi import APIRouter


router = APIRouter(tags=["system"])


@router.get("/health")
async def health_check() -> dict[str, str]:
    """返回服务存活状态，供部署探针和人工检查使用。"""
    return {"status": "ok"}
