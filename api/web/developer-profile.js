'use strict';
window.pushokDeveloper = (() => {
  const endpoint = '/community/developer';

  async function open(target) {
    // Переход по якорю профиля и фоновая перерисовка не должны сбрасывать черновик.
    if (target.querySelector('form')) return;
    const session = token;
    const card = el('section', undefined, 'developer-card');
    card.setAttribute('aria-label', 'О разработчике');
    const header = el('header', undefined, 'developer-header');
    const title = el('h2', 'О разработчике');
    const brand = el('img', undefined, 'developer-mascot');
    brand.src = '/assets/pushok-pencil.png';
    brand.alt = '';
    header.append(brand, title);
    const body = el('div', undefined, 'developer-copy');
    const feedback = el('p', 'Загружаем описание…', 'developer-status');
    feedback.setAttribute('role', 'status');
    card.append(header, body, feedback);
    target.replaceChildren(card);
    const current = () => token === session && card.isConnected;

    function render(data) {
      body.replaceChildren(el('p', data.description), el('h3', 'История программы'), el('p', data.history));
      if (me.is_owner) {
        const edit = button('Редактировать', () => editor(data));
        edit.className = 'developer-edit';
        edit.prepend(window.pushokViews.icon('edit'));
        body.append(edit);
      }
    }

    function field(label, value, limit, rows) {
      const input = el('textarea');
      input.value = value;
      input.maxLength = limit;
      input.rows = rows;
      input.required = true;
      const wrapper = el('label', label);
      wrapper.append(input);
      return wrapper;
    }

    function editor(data) {
      const form = el('form', undefined, 'developer-editor');
      const description = field('Краткое описание', data.description, 400, 3);
      const history = field('История программы', data.history, 1600, 6);
      const actions = el('div', undefined, 'developer-actions');
      const save = el('button', 'Сохранить', 'primary');
      save.type = 'submit';
      const cancel = button('Отмена', () => {
        render(data);
        feedback.replaceChildren();
        body.querySelector('.developer-edit')?.focus();
      });
      actions.append(save, cancel);
      form.append(description, history, actions);
      body.replaceChildren(form);
      feedback.replaceChildren();
      description.control.focus();
      form.addEventListener('submit', async event => {
        event.preventDefault();
        if (save.disabled || !form.reportValidity()) return;
        save.disabled = cancel.disabled = true;
        feedback.textContent = 'Сохраняем…';
        try {
          const saved = await api(endpoint, {method: 'PUT', body: {
            description: description.control.value.trim(), history: history.control.value.trim(), revision: data.revision
          }});
          if (!current()) return;
          render(saved);
          feedback.textContent = 'Изменения сохранены.';
          body.querySelector('.developer-edit')?.focus();
        } catch (error) {
          if (!current()) return;
          if (error.code === 'developer_profile_changed') {
            const reload = button('Открыть сохранённую версию', () => {
              form.remove();
              return open(target);
            });
            feedback.replaceChildren('Текст уже изменён в другом окне. Ваш черновик остался в форме. ', reload);
          } else {
            feedback.textContent = error.message;
          }
        } finally {
          save.disabled = cancel.disabled = false;
        }
      });
    }

    try {
      const data = await api(endpoint);
      if (current()) {
        render(data);
        feedback.replaceChildren();
      }
    } catch (error) {
      if (current()) feedback.replaceChildren(error.message + ' ', button('Повторить', () => open(target)));
    }
  }
  return {open};
})();
