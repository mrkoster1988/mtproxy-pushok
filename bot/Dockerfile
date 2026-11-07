FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin pushok
COPY bot /app/bot
USER 10001:10001

CMD ["python", "-m", "bot.main"]
