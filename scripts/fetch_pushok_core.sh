#!/usr/bin/env bash
# ПОЛУЧЕНИЕ исходников Telemt для самостоятельной сборки Pushok. Не устанавливает сервис.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK="$ROOT/third_party/telemt/UPSTREAM.lock"
TAG=3.5.17
SHA=66708d2866f3f3a4541c4fa67984d408e71a8af4
URL=https://github.com/telemt/telemt.git
OUT="$ROOT/.build/pushok-core/upstream-$SHA"
MODE="${1:---plan}"
if [[ "$MODE" != "--plan" && "$MODE" != "--fetch" ]]; then
  echo "Использование: bash scripts/fetch_pushok_core.sh [--plan|--fetch]" >&2
  exit 2
fi
grep -Fxq "repository=$URL" "$LOCK"
grep -Fxq "tag=$TAG" "$LOCK"
grep -Fxq "commit=$SHA" "$LOCK"
echo "Pushok Core: upstream tag $TAG, commit $SHA"
echo "Хранилище: .build/pushok-core/ (не устанавливает proxy, не меняет .env)"
if [[ "$MODE" == "--plan" ]]; then
  echo "ПЛАН: только проверка lock; файлы и сервисы не менялись."
  exit 0
fi
if (( EUID == 0 )); then
  echo "Сборочные исходники нужно получать обычным пользователем, не root." >&2
  exit 1
fi
command -v git >/dev/null || { echo "Нужен git." >&2; exit 1; }
if [[ -L "$ROOT/.build" || -L "$ROOT/.build/pushok-core" || -L "$OUT" ]]; then
  echo "Символьные ссылки в каталоге сборки запрещены." >&2
  exit 1
fi
if [[ -e "$OUT" ]]; then
  [[ -d "$OUT/.git" ]] || { echo "Каталог существует и не является рабочей копией git. Отказ без перезаписи." >&2; exit 1; }
  got="$(git -C "$OUT" rev-parse HEAD)"
  [[ "$got" == "$SHA" ]] || { echo "Неверный commit в существующей копии; отказ." >&2; exit 1; }
  echo "Проверенный исходный commit уже загружен. Повторная загрузка не нужна."
  exit 0
fi
umask 077
mkdir -p "$ROOT/.build/pushok-core"
TMP="$(mktemp -d "$ROOT/.build/pushok-core/.download.XXXXXXXX")"
cleanup(){ rm -rf -- "$TMP"; }
trap cleanup EXIT
git -c core.hooksPath=/dev/null init -q "$TMP"
git -C "$TMP" remote add origin "$URL"
git -C "$TMP" -c core.hooksPath=/dev/null fetch --depth 1 --no-tags origin "$SHA"
git -C "$TMP" -c core.hooksPath=/dev/null checkout -q --detach FETCH_HEAD
got="$(git -C "$TMP" rev-parse HEAD)"
[[ "$got" == "$SHA" ]] || { echo "Контроль Git SHA не пройден." >&2; exit 1; }
cmp -s "$ROOT/third_party/telemt/LICENSE" "$TMP/LICENSE" || {
  echo "Лицензия upstream отличается от зафиксированной копии. Отказ." >&2; exit 1;
}
grep -Eq '^version[[:space:]]*=[[:space:]]*"3\.5\.17"' "$TMP/Cargo.toml" || {
  echo "Версия Cargo.toml не соответствует lock. Отказ." >&2; exit 1;
}
[[ -f "$TMP/Cargo.lock" && -d "$TMP/src" ]] || { echo "Исходный код неполный." >&2; exit 1; }
mv -- "$TMP" "$OUT"
trap - EXIT
echo "ИСТОЧНИК ЗАГРУЖЕН: точный commit, Cargo.lock и лицензия проверены."
echo "Ничего не компилировалось и не запускалось."
