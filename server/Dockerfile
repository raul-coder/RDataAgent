FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 依赖先行，充分利用镜像层缓存
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]" || pip install --no-cache-dir \
    fastapi "uvicorn[standard]" pydantic pydantic-settings "sqlalchemy>=2.0" \
    alembic asyncpg "psycopg[binary]" redis "python-jose[cryptography]" \
    "passlib[bcrypt]" "sqlglot>=23" jinja2 python-multipart httpx \
    greenlet openpyxl litellm pillow

COPY . .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
