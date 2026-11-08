#!/usr/bin/env bash
# Собственная сборка pinned Telemt с документированным патчем Pushok. Не меняет VPS.
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
  bash "$ROOT/scripts/apply_pushok_core_patch.sh" --plan
  echo "ПЛАН: Rust-патч -> cargo build --release --locked -> --pushok-build-info; без выполнения."
  exit 0
fi
if (( EUID == 0 )); then echo "Запустите сборку без sudo." >&2; exit 1; fi
[[ ! -L "$ROOT/.build" && ! -L "$ROOT/.build/pushok-core" && ! -L "$SRC" ]] || {
  echo "Символьные ссылки в каталоге сборки запрещены." >&2; exit 1;
}
[[ -d "$SRC/.git" ]] || { echo "Сначала: bash scripts/fetch_pushok_core.sh --fetch" >&2; exit 1; }
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$SHA" ]] || { echo "Upstream commit не совпадает." >&2; exit 1; }
cmp -s "$ROOT/third_party/telemt/LICENSE" "$SRC/LICENSE" || { echo "Upstream license не совпадает." >&2; exit 1; }
command -v cargo >/dev/null || { echo "Нужен Rust Cargo (upstream требует rustc >= 1.88)." >&2; exit 1; }
command -v rustc >/dev/null || { echo "Не найден rustc." >&2; exit 1; }
command -v python3 >/dev/null || { echo "Нужен python3 для проверки сборки." >&2; exit 1; }
cargo --version
rustc --version
OUTPUT_NAME="pushok-core-0.027-$SHA"
[[ ! -L "$OUT" ]] || { echo "Папка артефактов не должна быть symlink." >&2; exit 1; }
[[ ! -e "$OUT/$OUTPUT_NAME" ]] || {
  echo "Артефакт уже существует, перезапись запрещена." >&2; exit 1;
}
# Только после проверок разрешаем изменение локального дерева upstream.
bash "$ROOT/scripts/apply_pushok_core_patch.sh" --apply
# Модуль идентификации проверяем без старта MTProxy.
RUST_TEST_TMP="$(mktemp "$ROOT/.build/pushok-core/.rust-tests.XXXXXXXX")"
trap 'rm -f "$RUST_TEST_TMP"' EXIT
rustc --edition=2024 --test "$SRC/src/pushok_build.rs" -o "$RUST_TEST_TMP"
"$RUST_TEST_TMP"
# Сборка с неизменным upstream Cargo.lock.
(cd "$SRC" && cargo build --release --locked)
BIN="$SRC/target/release/telemt"
[[ -f "$BIN" ]] || { echo "Бинарный файл не получен." >&2; exit 1; }
"$BIN" --pushok-build-info | python3 -c '
import json, sys
x = json.load(sys.stdin)
assert x["product"] == "Pushok Source Core"
assert x["version"] == "0.027"
assert x["upstream_commit"] == "66708d2866f3f3a4541c4fa67984d408e71a8af4"
assert x["patch"] == "cli-build-info-001"
assert x["proxy_started"] is False
'
mkdir -p "$OUT"
install -m 0700 "$BIN" "$OUT/$OUTPUT_NAME"
cp -- "$SRC/LICENSE" "$OUT/TELEMT-LICENSE"
cp -- "$ROOT/third_party/telemt/NOTICE.md" "$OUT/NOTICE.md"
cp -- "$ROOT/third_party/telemt/PATCHES.md" "$OUT/PATCHES.md"
(cd "$OUT" && sha256sum "$OUTPUT_NAME" > "$OUTPUT_NAME.sha256")
trap - EXIT
rm -f "$RUST_TEST_TMP"
echo "СБОРКА ГОТОВА: Pushok v0.027 с патчем cli-build-info-001, лицензией и sha256."
echo "На VPS ничего не установлено; MTProxy и работающие сервисы не изменены."
