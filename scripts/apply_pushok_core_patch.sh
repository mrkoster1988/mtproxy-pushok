#!/usr/bin/env bash
# Применение ТОЛЬКО нашего проверенного Rust-патча к Telemt upstream.
# Не запускает proxy, не обращается к systemd/Docker и не открывает порты.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SHA=66708d2866f3f3a4541c4fa67984d408e71a8af4
SRC="$ROOT/.build/pushok-core/upstream-$SHA"
PATCH="$ROOT/third_party/telemt/patches/0001-pushok-build-info.patch"
MODE="${1:---plan}"

if [[ $# -gt 1 ]] || [[ "$MODE" != "--plan" && "$MODE" != "--check" && "$MODE" != "--apply" ]]; then
  echo "Использование: bash scripts/apply_pushok_core_patch.sh [--plan|--check|--apply]" >&2
  exit 2
fi
[[ -f "$PATCH" ]] || { echo "Не найден фиксированный Rust-патч Pushok." >&2; exit 1; }
if [[ "$MODE" == "--plan" ]]; then
  echo "ПЛАН: патч 0001 добавит --pushok-build-info без запуска proxy."
  echo "Изменяются только src/main.rs и новый src/pushok_build.rs в локальной .build/."
  exit 0
fi
if (( EUID == 0 )); then echo "Не запускайте патчирование от root." >&2; exit 1; fi
command -v git >/dev/null || { echo "Требуется git." >&2; exit 1; }
if [[ -L "$ROOT/.build" || -L "$ROOT/.build/pushok-core" || -L "$SRC" ]]; then
  echo "Символьные ссылки в сборочном каталоге запрещены." >&2
  exit 1
fi
[[ -d "$SRC/.git" && -f "$SRC/LICENSE" ]] || {
  echo "Сначала получите pinned upstream: bash scripts/fetch_pushok_core.sh --fetch" >&2
  exit 1
}
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$SHA" ]] || {
  echo "Неверный commit источника. Отказ без изменений." >&2; exit 1
}
cmp -s "$ROOT/third_party/telemt/LICENSE" "$SRC/LICENSE" || {
  echo "Исходная лицензия изменена. Отказ." >&2; exit 1
}
# Только два ожидаемых пути; остальные изменения чужого дерева запрещены.
if git -C "$SRC" apply --reverse --check "$PATCH" 2>/dev/null; then
  changed="$(git -C "$SRC" diff --name-only)"
  untracked="$(git -C "$SRC" ls-files --others --exclude-standard)"
  [[ "$changed" == "src/main.rs" && "$untracked" == "src/pushok_build.rs" ]] || {
    echo "В upstream есть посторонние изменения. Отказ." >&2; exit 1
  }
  echo "Pushok Rust patch 0001 уже применён. Повторного изменения нет."
  exit 0
fi
[[ -z "$(git -C "$SRC" status --porcelain --untracked-files=all)" ]] || {
  echo "В upstream есть незакоммиченные изменения. Отказ без перезаписи." >&2; exit 1
}
git -C "$SRC" apply --check "$PATCH"
if [[ "$MODE" == "--check" ]]; then
  echo "ПРОВЕРЕНО: pinned source совместим с patch 0001. Файлы не менялись."
  exit 0
fi
git -C "$SRC" apply "$PATCH"
git -C "$SRC" diff --check
[[ -f "$SRC/src/pushok_build.rs" ]] || {
  echo "Модуль идентификации не появился." >&2; exit 1
}
echo "ПРИМЕНЕНО: модификация Rust-исходника Pushok v0.027; сервисы не запускались."
