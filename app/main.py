from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="PaperMind")

class Item(BaseModel):
    name: str
    price: float

@app.get("/")
async def read_root():
    return {"message": "Hello PaperMind"}

@app.post("/items")
async def create_item(item: Item):
    return {"message": f"created {item.name}", "price": item.price}

@app.get("/health")
async def health_check():
    """健康检查接口，Docker 部署时会用到"""
    return {"status": "ok", "version": "0.1.0"}