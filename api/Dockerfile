FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv/mtproxy-pushok

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && mkdir -p /var/lib/mtproxy-pushok/payment-proofs \
    && chown -R 10001:10001 /var/lib/mtproxy-pushok

COPY app ./app
COPY web ./web

# git обновлятора работает с umask 077; код должен читаться UID 10001.
# Нормализуем только код и статику, не каталоги данных и секретов.
RUN find app web -type d -exec chmod 0755 {} + \
    && find app web -type f -exec chmod 0644 {} +

USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
