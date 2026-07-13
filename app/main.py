from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="PaperMind", version="0.1.0")

@app.get("/health")
async def health_check():
    """返回服务存活状态，供测试、部署探针和人工检查使用。"""
    return {"status": "ok"}

class Item(BaseModel):
    name: str
    price: float

@app.get("/")
async def read_root():
    return {"message": "Hello PaperMind"}

@app.post("/items")
async def create_item(item: Item):
    return {"message": f"created {item.name}", "price": item.price}
