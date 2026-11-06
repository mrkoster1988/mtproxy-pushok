import os

import psycopg
from fastapi import FastAPI, HTTPException, status

app = FastAPI(
    title="MTProxy Pushok API",
    version="0.003",
    description="Начальный API кабинета спонсора MTProxy.",
)

DATABASE_URL = os.environ["DATABASE_URL"]


@app.get("/health/live", tags=["health"])
def live() -> dict[str, str]:
    """Проверка, что процесс API запущен."""
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def ready() -> dict[str, str]:
    """Проверка доступности PostgreSQL для обработки запросов."""
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3) as connection:
            connection.execute("SELECT 1")
    except psycopg.Error as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database_unavailable",
        ) from exc

    return {"status": "ready", "database": "ok"}
