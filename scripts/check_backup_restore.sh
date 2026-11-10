#!/bin/sh
set -eu
# Запускается исключительно в compose.smoke.yaml, без production-данных.
psql -v ON_ERROR_STOP=1 -f /migrations/002_telegram_auth.sql
psql -v ON_ERROR_STOP=1 -f /migrations/002_telegram_auth.sql
psql -v ON_ERROR_STOP=1 -c "INSERT INTO users (telegram_user_id, first_name) VALUES (99000001, 'smoke-persisted');"
printf 'pushok-proof-persistence\n' > /proofs/smoke-persisted.txt
mkdir -p /proofs/placement-evidence
printf 'pushok-evidence-smoke-fixture\n' > /proofs/placement-evidence/smoke-evidence.txt
pg_dump -Fc --file=/tmp/pushok-smoke.dump
createdb pushok_smoke_restored
pg_restore --exit-on-error --no-owner --no-privileges -d pushok_smoke_restored /tmp/pushok-smoke.dump
test "$(psql -At -d pushok_smoke_restored -c "SELECT count(*) FROM users WHERE telegram_user_id=99000001 AND first_name='smoke-persisted'")" = 1
test "$(psql -At -d pushok_smoke_restored -c "SELECT price_kopecks FROM tariffs WHERE name='1 день'")" = 30000
tar -cf /tmp/pushok-proofs.tar -C /proofs .
mkdir /tmp/restored-proofs
tar -xf /tmp/pushok-proofs.tar -C /tmp/restored-proofs
cmp /proofs/smoke-persisted.txt /tmp/restored-proofs/smoke-persisted.txt
cmp /proofs/placement-evidence/smoke-evidence.txt /tmp/restored-proofs/placement-evidence/smoke-evidence.txt
dropdb pushok_smoke_restored
echo "BACKUP RESTORE OK: PostgreSQL dump and proof files."
