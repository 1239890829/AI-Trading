FROM python:3.11-slim

WORKDIR /app
# 优先用锁定版本构建（技术评审 B3：requirements 全 >= 不可复现，
# 一次重装可能拉入破坏性升级）。lock 由 `pip freeze` 从验证过的 venv 生成；
# lock 缺失时回退到 requirements.txt（本地开发场景）。
COPY requirements.txt requirements.lock ./
RUN if [ -s requirements.lock ]; then pip install --no-cache-dir -r requirements.lock; \
    else pip install --no-cache-dir -r requirements.txt; fi
COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
