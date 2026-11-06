import os
from uuid import UUID

import psycopg
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel

app = FastAPI(
    title="MTProxy Pushok API",
    version="0.003",
    description="Начальный API кабинета спонсора MTProxy.",
)

DATABASE_URL = os.environ["DATABASE_URL"]


class TariffResponse(BaseModel):
    id: UUID
    name: str
    duration_seconds: int
    price_kopecks: int
    currency: str


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


@app.get("/api/v1/tariffs", response_model=list[TariffResponse], tags=["tariffs"])
def list_tariffs() -> list[TariffResponse]:
    """Вернуть действующие тарифы для страницы выбора размещения."""
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3) as connection:
            rows = connection.execute(
                """
                SELECT id, name, duration_seconds, price_kopecks, currency
                FROM tariffs
                WHERE is_active = TRUE
                ORDER BY duration_seconds, price_kopecks
                """
            ).fetchall()
    except psycopg.Error as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database_unavailable",
        ) from exc

    return [
        TariffResponse(
            id=row[0],
            name=row[1],
            duration_seconds=row[2],
            price_kopecks=row[3],
            currency=row[4].strip(),
        )
        for row in rows
    ]
