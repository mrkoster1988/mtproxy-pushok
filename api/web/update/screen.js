'use strict';
const title = document.querySelector('h1');
const stage = document.querySelector('#stage');
const note = document.querySelector('#note');
const progress = document.querySelector('#progress');
const retry = document.querySelector('#retry');
const embedded = new URLSearchParams(location.search).has('embedded');
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
retry.addEventListener('click', check);
void check();
