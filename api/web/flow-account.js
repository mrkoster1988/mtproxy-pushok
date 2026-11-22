'use strict';

// A public confirmation code never authenticates Flow. The device secret and
// the dedicated Flow bearer are not available to this page.
window.pushokFlowAccount = (() => {
  const codePattern = /^[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{12}$/;
  const scopes = ['profile:read', 'wallet:read'];
  let code = '', context = null, epoch = 0, controller = null;
  let busy = false, deadline = 0, timer = null, dialog = null, intro = null;
  let invalid = false;

  const node = (tag, text, className) => {
    const item = document.createElement(tag);
    if (text !== undefined) item.textContent = text;
    if (className) item.className = className;
    return item;
  };
  const formatted = value => value.match(/.{1,4}/g).join(' ');
  const sameAccount = value => Boolean(value && value === context &&
    value.session === token && value.userId === me?.id);
  const current = (value, generation) => sameAccount(value) && generation === epoch;

  function stopRequest() {
    epoch++;
    controller?.abort();
    controller = null;
    clearInterval(timer);
    timer = null;
    busy = false;
  }

  function reset() {
    stopRequest();
    code = '';
    context = null;
    deadline = 0;
    intro?.remove();
    intro = null;
    if (dialog?.open) dialog.close();
    dialog?.remove();
    dialog = null;
  }

  function action(text, id, callback, className = '') {
    const result = node('button', text, className);
    // Cabinet controls may replace ordinary button labels with icons. Consent
    // actions must retain their explicit, visible text.
    result.classList.add('labeled-action');
    result.type = 'button';
    result.id = id;
    result.addEventListener('click', callback);
    return result;
  }

  async function request(path, body) {
    const active = new AbortController();
    controller = active;
    const timeout = setTimeout(() => active.abort(), 15000);
    try { return await api(path, {method: 'POST', body, signal: active.signal}); }
    finally { clearTimeout(timeout); }
  }

  function frame(title) {
    intro?.remove();
    intro = null;
    if (!dialog) {
      dialog = node('dialog', undefined, 'flow-auth-dialog');
      dialog.id = 'flow-account-dialog';
      dialog.setAttribute('aria-labelledby', 'flow-account-title');
      // Escape dismisses this local request; it must never approve or claim a
      // server-side denial. The explicit cancel button makes that decision.
      dialog.addEventListener('cancel', event => {
        event.preventDefault();
        if (!busy) reset();
      });
      document.body.append(dialog);
    }
    const heading = node('h2', title);
    heading.id = 'flow-account-title';
    dialog.replaceChildren(heading);
    if (!dialog.open) dialog.showModal();
    return dialog;
  }

  function info(text, className = '') {
    return node('p', text, className);
  }

  function terminal(status) {
    stopRequest();
    code = '';
    context = null;
    const approved = status === 'approved';
    const panel = frame(approved ? 'Подключение разрешено' : 'Запрос отклонён');
    panel.dataset.state = status;
    panel.append(info(approved
      ? 'Вернитесь в Pushok Flow. Приложение получит ваш профиль и баланс кабинета MTProxy.'
      : 'Доступ к аккаунту не предоставлен. Вернитесь в Pushok Flow.'),
      action('Закрыть', 'flow-account-close', reset));
  }

  function unavailable(message) {
    stopRequest();
    code = '';
    context = null;
    const panel = frame('Запрос недоступен');
    panel.dataset.state = 'unavailable';
    panel.append(info(message), action('Закрыть', 'flow-account-close', reset));
  }

  function identity(panel, value) {
    const person = node('div', undefined, 'flow-auth-identity');
    person.id = 'flow-account-identity';
    const name = [value.firstName, value.lastName].filter(Boolean).join(' ') || 'Аккаунт MTProxy';
    person.append(node('strong', name));
    if (value.telegramId) person.append(node('span', 'Telegram ID: ' + value.telegramId));
    person.append(info('Проверьте, что это ваш аккаунт. Для другого аккаунта закройте запрос и повторите вход через нужный Telegram.'));
    panel.append(person);
  }

  function retry(message, value) {
    if (!sameAccount(value)) return;
    clearInterval(timer);
    timer = null;
    busy = false;
    const panel = frame('Проверьте состояние запроса');
    panel.dataset.state = 'retry';
    const actions = node('div', undefined, 'flow-auth-retry-actions');
    actions.append(action('Проверить состояние', 'flow-account-retry', () => review(value)),
      action('Закрыть', 'flow-account-close', reset, 'secondary'));
    panel.append(info(message), actions);
  }

  function validReview(data) {
    return Boolean(data && typeof data === 'object' && !Array.isArray(data) &&
      Object.keys(data).length === 5 && data.client_name === 'Pushok Flow' &&
      data.user_code === code && codePattern.test(data.user_code) &&
      Array.isArray(data.scopes) && data.scopes.length === scopes.length &&
      scopes.every(scope => data.scopes.includes(scope)) &&
      Number.isInteger(data.expires_in) && data.expires_in > 0 && data.expires_in <= 300 &&
      ['pending', 'approved', 'denied'].includes(data.status));
  }

  function renderPending(data, value) {
    busy = false;
    deadline = Date.now() + data.expires_in * 1000;
    const panel = frame('Подключить аккаунт к Pushok Flow?');
    panel.dataset.state = 'pending';
    identity(panel, value);
    const display = node('p', formatted(code), 'flow-auth-code');
    display.id = 'flow-account-code';
    panel.append(info('Сверьте этот код с кодом на экране Pushok Flow:'), display);
    const permissions = node('ul', undefined, 'flow-auth-scopes');
    permissions.append(node('li', 'Просмотр имени, роли и статуса подтверждения профиля'),
      node('li', 'Просмотр баланса кабинета MTProxy: рубли, бонусы и Stars'));
    panel.append(permissions, info('Доступ только для просмотра. Оплата и управление кабинетом остаются в MTProxy.', 'flow-auth-note'));
    const expiry = info('', 'flow-auth-expiry');
    expiry.id = 'flow-account-expiry';
    expiry.setAttribute('aria-live', 'off');
    panel.append(expiry);
    const actions = node('div', undefined, 'flow-auth-actions');
    const deny = action('Отмена', 'flow-account-deny', () => decide('deny', value), 'secondary');
    const approve = action('Подключить', 'flow-account-approve', () => decide('approve', value));
    actions.append(deny, approve);
    panel.append(actions);
    deny.focus();
    const updateExpiry = () => {
      if (!sameAccount(value)) { reset(); return; }
      const remaining = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      expiry.textContent = remaining ? 'Запрос истечёт через ' + remaining + ' сек.' : 'Проверяем результат…';
      if (!remaining && !busy) unavailable('Срок запроса истёк. Начните вход заново в Pushok Flow.');
    };
    clearInterval(timer);
    timer = setInterval(updateExpiry, 1000);
    updateExpiry();
  }

  async function review(value) {
    if (!code || !sameAccount(value)) return;
    stopRequest();
    busy = true;
    const generation = epoch;
    const panel = frame('Проверяем запрос Pushok Flow…');
    panel.dataset.state = 'loading';
    panel.append(info('Подтверждение появится после проверки запроса.'));
    try {
      const data = await request('/flow/device/review', {user_code: code});
      if (!current(value, generation)) return;
      if (!validReview(data)) {
        unavailable('Не удалось проверить параметры доступа. Начните вход заново в Pushok Flow.');
        return;
      }
      if (data.status !== 'pending') terminal(data.status);
      else renderPending(data, value);
    } catch (error) {
      if (!current(value, generation)) return;
      if ([403, 404, 409, 410].includes(error.status)) {
        unavailable('Запрос истёк, уже обработан или недоступен этому аккаунту. Начните вход заново в Pushok Flow.');
      } else {
        retry('Не удалось проверить запрос. Повторите проверку, когда восстановится связь.', value);
      }
    } finally {
      if (current(value, generation)) { busy = false; controller = null; }
    }
  }

  async function decide(decision, value) {
    if (busy || !code || !sameAccount(value) || dialog?.dataset.state !== 'pending') return;
    if (Date.now() >= deadline) {
      unavailable('Срок запроса истёк. Начните вход заново в Pushok Flow.');
      return;
    }
    busy = true;
    const generation = epoch;
    const buttons = [...dialog.querySelectorAll('button')];
    buttons.forEach(button => { button.disabled = true; });
    try {
      const data = await request('/flow/device/decision', {user_code: code, decision});
      if (!current(value, generation)) return;
      const expected = decision === 'approve' ? 'approved' : 'denied';
      if (!data || Object.keys(data).length !== 1 || data.status !== expected) {
        retry('Ответ сервера не подтверждён. Проверьте состояние запроса перед следующим действием.', value);
        return;
      }
      terminal(data.status);
    } catch (error) {
      if (!current(value, generation)) return;
      if ([403, 404, 409, 410].includes(error.status)) {
        unavailable('Запрос истёк, уже обработан или недоступен этому аккаунту. Начните вход заново в Pushok Flow.');
      } else {
        retry('Ответ не получен. Проверьте состояние запроса: подтверждение могло сохраниться на сервере.', value);
      }
    } finally {
      if (current(value, generation)) { busy = false; controller = null; }
    }
  }

  function authenticated() {
    if (!code || !token || !me?.id) return;
    if (context) {
      if (!sameAccount(context)) reset();
      return;
    }
    context = {
      session: token, userId: me.id, firstName: me.first_name,
      lastName: me.last_name, telegramId: me.telegram_user_id,
    };
    void review(context);
  }

  // This module loads before app.js. Capture only the public code, before the
  // ordinary Telegram Login Widget can create third-party requests.
  try {
    const url = new URL(location.href);
    const values = url.searchParams.getAll('flow');
    if (values.length) {
      code = values.length === 1 && codePattern.test(values[0]) ? values[0] : '';
      invalid = !code;
      url.searchParams.delete('flow');
      history.replaceState(null, '', url);
      if (code) {
        intro = node('section', undefined, 'panel flow-auth-intro');
        intro.id = 'flow-account-intro';
        intro.append(node('h2', 'Вход в Pushok Flow'),
          info('Войдите в существующий аккаунт через Telegram. Затем кабинет попросит подтвердить подключение.'),
          node('p', formatted(code), 'flow-auth-code'));
        document.querySelector('main').prepend(intro);
      }
    }
  } catch (_) {
    code = '';
    invalid = true;
  }
  if (invalid) unavailable('Ссылка недействительна. Начните вход заново в Pushok Flow.');
  return {authenticated, reset};
})();
