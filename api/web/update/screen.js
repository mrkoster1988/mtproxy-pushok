'use strict';
const title = document.querySelector('h1');
const stage = document.querySelector('#stage');
const note = document.querySelector('#note');
const progress = document.querySelector('#progress');
const retry = document.querySelector('#retry');
const query = new URLSearchParams(location.search);
const embedded = query.has('embedded');
const requestedAt = Date.parse(query.get('requested_at') || '');
function belongsToRequest(status) {
  const at = Date.parse(status?.at || '');
  return !Number.isFinite(requestedAt) || (Number.isFinite(at) && at >= requestedAt);
}
function returnToCabinet() {
  try { window.parent.location.reload(); }
  catch { location.replace('/miniapp'); }
}
const steps = {
  preflight: 'Проверяем окружение',
  backup: 'Создаём резервную копию',
  source: 'Получаем новую версию',
  build: 'Собираем приложение',
  migration: 'Обновляем базу данных',
  restart: 'Перезапускаем кабинет',
  health: 'Проверяем работу кабинета',
  proxy: 'Проверяем подключение к прокси',
};
if (embedded) {
  title.textContent = 'Идёт обновление';
  stage.textContent = 'Ожидаем запуск установки…';
}
async function json(path) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 3500);
  try {
    const response = await fetch(path, {cache:'no-store', signal:controller.signal});
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  } finally {
    clearTimeout(timeout);
  }
}
let timer, busy = false;
async function check() {
  if (busy) return;
  busy = true;
  clearTimeout(timer);
  const status = await json('/update/status.json');
  if (status?.state === 'running') {
    title.textContent = 'Идёт обновление';
    stage.textContent = steps[status.stage] || 'Готовимся к установке…';
    const completed = Object.keys(steps).indexOf(status.stage);
    if (completed >= 0) progress.value = completed;
    else progress.removeAttribute('value');
    note.textContent = 'Кабинет откроется автоматически.';
    retry.hidden = true;
  } else if (embedded && status?.state === 'succeeded' && belongsToRequest(status)) {
    title.textContent = 'Обновление установлено';
    stage.textContent = 'Открываем обновлённый кабинет…';
    note.textContent = 'Mini App перезапустится автоматически.';
    progress.value = 8;
    const ready = await json('/health/ready');
    const current = await json('/update/status.json');
    if (ready?.status === 'ready' && current?.state === 'succeeded' && belongsToRequest(current)) {
      returnToCabinet();
      return;
    }
  } else if (embedded && status?.state === 'failed' && belongsToRequest(status)) {
    title.textContent = 'Обновление не завершилось';
    stage.textContent = 'Установщик остановился с ошибкой.';
    note.textContent = 'Перезагрузите Mini App и проверьте статус обновления.';
    progress.removeAttribute('value');
    retry.textContent = 'Вернуться в кабинет';
    retry.hidden = false;
  } else if (embedded && Number.isFinite(requestedAt) && Date.now() - requestedAt > 120000) {
    title.textContent = 'Установка ещё не запустилась';
    stage.textContent = 'Сервер не начал обновление.';
    note.textContent = 'Можно вернуться в кабинет. Статус установки останется доступен в профиле.';
    retry.textContent = 'Вернуться в кабинет';
    retry.hidden = false;
  } else if (!embedded) {
    const ready = await json('/health/ready');
    if (ready?.status === 'ready') {
      // Повторная проверка исключает возврат в кабинет между двумя шагами обновления.
      const current = await json('/update/status.json');
      if (current && current.state !== 'running') {
        const destination = location.pathname === '/update/screen.html' ? '/miniapp' : location.pathname;
        location.replace(destination + location.search + location.hash);
        return;
      }
    }
    const failed = status?.state === 'failed';
    title.textContent = failed ? 'Обновление прервалось' : 'Восстанавливаем связь';
    stage.textContent = failed ? 'Проверяем, доступен ли кабинет.' : 'Кабинет временно недоступен.';
    note.textContent = 'Продолжаем проверять подключение.';
    progress.removeAttribute('value');
    retry.hidden = false;
  }
  busy = false;
  timer = setTimeout(check, 2500);
}
retry.addEventListener('click', () => embedded ? returnToCabinet() : check());
void check();
