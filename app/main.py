from fastapi import FastAPI

app = FastAPI(title="PaperMind")

@app.get("/")
def read_root():
    return {"message": "Hello PaperMind"}

@app.get("/health")
def health_check():
    """健康检查接口，Docker 部署时会用到"""
    return {"status": "ok", "version": "0.1.0"}