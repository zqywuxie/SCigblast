/* Shared, dependency-free feedback for task actions. */
window.UI = (() => {
  const busyButtons = new WeakMap();
  let dialogNumber = 0;

  function setBusy(button, busy, label) {
    if (!button) return;
    if (busy) {
      if (!busyButtons.has(button)) busyButtons.set(button, {
        disabled: button.disabled,
        ariaBusy: button.getAttribute('aria-busy'),
        children: [...button.childNodes]
      });
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      button.classList.add('ui-busy');
      if (label !== undefined) button.textContent = label;
    } else {
      const previous = busyButtons.get(button);
      if (!previous) return;
      button.disabled = previous.disabled;
      if (previous.ariaBusy === null) button.removeAttribute('aria-busy');
      else button.setAttribute('aria-busy', previous.ariaBusy);
      button.classList.remove('ui-busy');
      button.replaceChildren(...previous.children);
      busyButtons.delete(button);
    }
  }

  function confirm({title = '确认操作', message = '', confirmLabel = '确认', danger = false} = {}) {
    return new Promise(resolve => {
      const returnFocus = document.activeElement;
      const modal = document.createElement('dialog');
      const id = `ui-confirm-${++dialogNumber}`;
      modal.className = 'ui-confirm-dialog';
      modal.setAttribute('aria-labelledby', `${id}-title`);
      modal.setAttribute('aria-describedby', `${id}-message`);
      modal.innerHTML = `<h2 id="${id}-title"></h2><p id="${id}-message"></p><footer><button type="button" class="button button-ghost" data-cancel>取消</button><button type="button" class="button ${danger ? 'button-danger' : 'button-primary'}" data-confirm></button></footer>`;
      modal.querySelector('h2').textContent = title;
      modal.querySelector('p').textContent = message;
      modal.querySelector('[data-confirm]').textContent = confirmLabel;
      let accepted = false, settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        modal.remove();
        if (returnFocus?.isConnected && !returnFocus.disabled) returnFocus.focus({preventScroll:true});
        resolve(accepted);
      };
      modal.querySelector('[data-cancel]').onclick = () => modal.close();
      modal.querySelector('[data-confirm]').onclick = () => { accepted = true; modal.close(); };
      modal.addEventListener('cancel', event => { event.preventDefault(); modal.close(); });
      modal.addEventListener('close', finish, {once:true});
      document.body.append(modal);
      modal.showModal();
      modal.querySelector('[data-cancel]').focus();
    });
  }

  return {confirm, setBusy};
})();
