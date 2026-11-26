FROM python:3.12-slim AS geo-data

# DB-IP City Lite (CC BY 4.0). Pin the archive and retain its attribution.
ADD https://registry.npmjs.org/@ip-location-db/dbip-city-mmdb/-/dbip-city-mmdb-2.3.2026060513.tgz /tmp/dbip-city.tgz
RUN echo '9132063b8ee6030cc1652a05f9523b940076f7f44bfd254ca6e47fb2a3953475  /tmp/dbip-city.tgz' | sha256sum -c - \
    && mkdir -p /usr/share/pushok \
    && tar xzf /tmp/dbip-city.tgz --strip-components=1 -C /usr/share/pushok package/dbip-city-ipv4.mmdb package/dbip-city-ipv6.mmdb package/DBIP-LICENSE \
    && chmod 644 /usr/share/pushok/* && rm /tmp/dbip-city.tgz

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv/mtproxy-pushok

# Кириллица и знак рубля в изображениях подарочных сертификатов.
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY --from=geo-data /usr/share/pushok/ /usr/share/pushok/

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
