#!/usr/bin/env bash
# Изолированная сборка upstream Rust-кода. НЕ меняет systemd, Docker, порты или MTProxy.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SHA=66708d2866f3f3a4541c4fa67984d408e71a8af4
SRC="$ROOT/.build/pushok-core/upstream-$SHA"
OUT="$ROOT/.build/pushok-core/output"
MODE="${1:---plan}"
if [[ "$MODE" != "--plan" && "$MODE" != "--build" ]]; then
  echo "Использование: bash scripts/build_pushok_core.sh [--plan|--build]" >&2
  exit 2
fi
echo "Pushok Core: сборка Rust source $SHA; файлы только внутри .build/"
if [[ "$MODE" == "--plan" ]]; then
  echo "ПЛАН: cargo build --release --locked; без выполнения."
  exit 0
fi
if (( EUID == 0 )); then echo "Запустите сборку без sudo." >&2; exit 1; fi
[[ -d "$SRC/.git" ]] || { echo "Сначала: bash scripts/fetch_pushok_core.sh --fetch" >&2; exit 1; }
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$SHA" ]] || { echo "Upstream commit не совпадает." >&2; exit 1; }
cmp -s "$ROOT/third_party/telemt/LICENSE" "$SRC/LICENSE" || { echo "Upstream license не совпадает." >&2; exit 1; }
command -v cargo >/dev/null || { echo "Нужен Rust Cargo (upstream требует rustc >= 1.88)." >&2; exit 1; }
command -v rustc >/dev/null || { echo "Не найден rustc." >&2; exit 1; }
cargo --version
rustc --version
# Используем upstream Cargo.lock, без автоматических изменений dependency graph.
(cd "$SRC" && cargo build --release --locked)
[[ -f "$SRC/target/release/telemt" ]] || { echo "Бинарный файл не получен." >&2; exit 1; }
mkdir -p "$OUT"
if [[ -e "$OUT/telemt-upstream-$SHA" ]]; then
  echo "Артефакт уже существует; перезапись запрещена." >&2
  exit 1
fi
install -m 0700 "$SRC/target/release/telemt" "$OUT/telemt-upstream-$SHA"
cp -- "$SRC/LICENSE" "$OUT/TELEMT-LICENSE"
cp -- "$ROOT/third_party/telemt/NOTICE.md" "$OUT/NOTICE.md"
(cd "$OUT" && sha256sum "telemt-upstream-$SHA" > "telemt-upstream-$SHA.sha256")
echo "СБОРКА ГОТОВА: неизменённый upstream binary + лицензия + checksum."
echo "На VPS ничего не установлено; Pushok-бинарник ещё не модифицирован."
