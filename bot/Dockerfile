FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin pushok
RUN install -d -m 0700 -o 10001 -g 10001 /var/lib/mtproxy-pushok/navigation
COPY bot /app/bot
# Исходники после закрытой umask хоста должны читаться непривилегированным ботом.
RUN find /app/bot -type d -exec chmod 0755 {} + \
    && find /app/bot -type f -exec chmod 0644 {} +
USER 10001:10001

CMD ["python", "-m", "bot.main"]
