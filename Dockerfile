FROM python:3.12-slim

WORKDIR /srv/gateway

COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic

RUN pip install --no-cache-dir ".[postgres]"

EXPOSE 8080

# 生产:先跑迁移再起服务(GW_AUTO_CREATE_TABLES=false 时完全依赖 alembic)
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8080"]
