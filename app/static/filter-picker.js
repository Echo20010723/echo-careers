(() => {
  const menu = document.createElement('div');
  menu.id = 'filter-picker-menu';
  menu.className = 'filter-picker-menu';
  menu.tabIndex = -1;
  menu.setAttribute('role', 'listbox');
  menu.hidden = true;
  document.body.append(menu);

  let current = null;
  let activeIndex = 0;
  let positionFrame = 0;

  function position() {
    if (!current) return;
    const { trigger } = current;
    const rect = trigger.getBoundingClientRect();
    const viewport = window.visualViewport;
    const leftEdge = (viewport?.offsetLeft ?? 0) + 8;
    const topEdge = (viewport?.offsetTop ?? 0) + 8;
    const rightEdge = leftEdge + (viewport?.width ?? document.documentElement.clientWidth) - 16;
    const bottomEdge = topEdge + (viewport?.height ?? document.documentElement.clientHeight) - 16;
    const anchor = trigger.closest('#discovery-settings');
    const clip = anchor?.getBoundingClientRect();
    if (!rect.width || rect.bottom <= Math.max(topEdge, clip?.top ?? topEdge) ||
        rect.top >= Math.min(bottomEdge, clip?.bottom ?? bottomEdge)) {
      close(false);
      return;
    }
    const width = Math.min(rightEdge - leftEdge, Math.max(rect.width, current.compact ? 190 : 212));
    const below = bottomEdge - rect.bottom - 6;
    const above = rect.top - topEdge - 6;
    const desired = Math.min(menu.scrollHeight + 2, 300);
    const opensUp = below < desired && above > below;
    const maxHeight = Math.max(0, Math.min(300, opensUp ? above : below));
    menu.style.width = width + 'px';
    menu.style.maxHeight = maxHeight + 'px';
    menu.style.left = Math.max(leftEdge, Math.min(rect.left, rightEdge - width)) + 'px';
    menu.style.top = (opensUp ? rect.top - 6 - menu.offsetHeight : rect.bottom + 6) + 'px';
    menu.dataset.side = opensUp ? 'top' : 'bottom';
  }

  function queuePosition() {
    if (!current || positionFrame) return;
    positionFrame = requestAnimationFrame(() => {
      positionFrame = 0;
      position();
    });
  }

  function close(restoreFocus = true) {
    if (!current) return;
    const { trigger } = current;
    current = null;
    menu.hidden = true;
    menu.removeAttribute('aria-activedescendant');
    trigger.setAttribute('aria-expanded', 'false');
    if (restoreFocus) trigger.focus({ preventScroll: true });
  }

  function makeActive(index, keyboard = false) {
    const previous = menu.children[activeIndex];
    previous?.removeAttribute('data-active');
    activeIndex = index;
    const option = menu.children[index];
    if (!option) return;
    if (keyboard) option.dataset.active = '';
    menu.setAttribute('aria-activedescendant', option.id);
    const top = option.offsetTop;
    if (top < menu.scrollTop) menu.scrollTop = top;
    if (top + option.offsetHeight > menu.scrollTop + menu.clientHeight) {
      menu.scrollTop = top + option.offsetHeight - menu.clientHeight;
    }
  }

  function draw() {
    const { select } = current;
    const fragment = document.createDocumentFragment();
    for (const [index, option] of Array.from(select.options).entries()) {
      const row = document.createElement('div');
      row.id = 'filter-picker-option-' + index;
      row.dataset.index = index;
      row.setAttribute('role', 'option');
      row.setAttribute('aria-selected', String(option.selected));
      if (option.disabled) row.setAttribute('aria-disabled', 'true');

      const text = document.createElement('span');
      text.textContent = option.label;
      if (option.dataset.description) {
        const description = document.createElement('small');
        description.textContent = ' · ' + option.dataset.description;
        text.append(description);
      }
      const check = document.createElement('span');
      check.className = 'filter-picker-check';
      check.textContent = '✓';
      check.setAttribute('aria-hidden', 'true');
      row.append(text, check);
      fragment.append(row);
    }
    menu.replaceChildren(fragment);
  }

  function open(picker, keyboard) {
    close(false);
    current = picker;
    draw();
    menu.setAttribute('aria-labelledby', picker.label.id);
    menu.hidden = false;
    picker.trigger.setAttribute('aria-expanded', 'true');
    menu.scrollTop = 0;
    position();
    if (!current) return;
    makeActive(picker.select.selectedIndex, keyboard);
    menu.focus({ preventScroll: true });
  }

  function choose(index) {
    const { select } = current;
    const option = select.options[index];
    if (!option || option.disabled) return;
    close();
    if (select.selectedIndex === index) return;
    select.selectedIndex = index;
    select.dispatchEvent(new Event('input', { bubbles: true }));
    select.dispatchEvent(new Event('change', { bubbles: true }));
  }

  menu.addEventListener('click', event => {
    const option = event.target.closest('[role=option]');
    if (option && current) choose(Number(option.dataset.index));
  });
  menu.addEventListener('keydown', event => {
    if (!current) return;
    if (event.key === 'Tab') {
      close();
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      close();
      return;
    }
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      choose(activeIndex);
      return;
    }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const enabled = Array.from(current.select.options, (option, index) => option.disabled ? -1 : index)
      .filter(index => index >= 0);
    if (!enabled.length) return;
    const focused = enabled.indexOf(activeIndex);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? enabled.length - 1
      : Math.max(0, Math.min(enabled.length - 1, focused + (event.key === 'ArrowDown' ? 1 : -1)));
    makeActive(enabled[next], true);
  });

  document.addEventListener('pointerdown', event => {
    if (current && !menu.contains(event.target) && !current.trigger.contains(event.target)) close();
  }, true);
  document.addEventListener('focusin', event => {
    if (current && !menu.contains(event.target) && !current.trigger.contains(event.target)) close(false);
  });
  document.addEventListener('scroll', event => {
    if (event.target !== menu) queuePosition();
  }, true);
  window.addEventListener('resize', queuePosition);
  window.visualViewport?.addEventListener('resize', queuePosition);
  window.visualViewport?.addEventListener('scroll', queuePosition);
  document.querySelector('.site-header nav').addEventListener('click', () => close(false));

  for (const select of document.querySelectorAll('select[data-picker]')) {
    const label = select.labels[0];
    const compact = select.dataset.picker === 'compact';
    const trigger = document.createElement('button');
    trigger.className = 'secondary filter-picker-trigger';
    trigger.type = 'button';
    trigger.id = select.id + '-picker';
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-controls', menu.id);
    trigger.setAttribute('aria-expanded', 'false');
    label.id = select.id + '-caption';
    label.htmlFor = trigger.id;

    const value = document.createElement('span');
    value.id = select.id + '-value';
    value.className = 'filter-picker-value';
    const arrow = document.createElement('span');
    arrow.className = 'filter-picker-arrow';
    arrow.setAttribute('aria-hidden', 'true');
    trigger.setAttribute('aria-labelledby', label.id + ' ' + value.id);
    trigger.append(value, arrow);
    select.after(trigger);
    select.hidden = true;
    const picker = { select, label, trigger, compact };

    function sync() {
      const option = select.selectedOptions[0];
      value.textContent = option ? [option.label, option.dataset.description].filter(Boolean).join(' · ') : '';
      trigger.disabled = select.disabled;
      if (current === picker) {
        const focused = menu.querySelector('[data-active]') !== null;
        draw();
        position();
        makeActive(select.selectedIndex, focused);
      }
    }
    select.addEventListener('change', sync);
    select.addEventListener('picker-sync', sync);
    new MutationObserver(sync).observe(select, { childList: true, attributes: true, attributeFilter: ['disabled'] });
    trigger.addEventListener('click', event => {
      if (current === picker) close();
      else open(picker, event.detail === 0);
    });
    trigger.addEventListener('keydown', event => {
      if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
      event.preventDefault();
      open(picker, true);
    });
    sync();
  }
})();
