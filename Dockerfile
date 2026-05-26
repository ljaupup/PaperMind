FROM python:3.11-slim

WORKDIR /app

# 先复制依赖文件，利用 Docker 缓存层
COPY pyproject.toml uv.lock ./

# 安装依赖（如果还没用 uv，用 pip 安装）
RUN pip install --no-cache-dir "fastapi[standard]" httpx uvicorn

# 再复制代码（代码改了不会重新装依赖）
COPY . .

# 声明服务端口
EXPOSE 8000

# 启动服务
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]