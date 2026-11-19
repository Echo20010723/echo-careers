(() => {
  const get = id => document.getElementById(id);
  const grid = document.querySelector('.discovery-workspace-grid');
  const workspace = document.querySelector('.discovery-workspace');
  const reading = document.querySelector('.discovery-results');
  const round = get('discovery-round');
  const roundBody = round.querySelector('.discovery-round-body');
  const storageKey = 'echo-careers-workspace-layout';
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(storageKey)) || {}; } catch {}
  const pixels = (value, fallback) => Number.isFinite(value) ? value : fallback;
  const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));

  function persist() {
    try { localStorage.setItem(storageKey, JSON.stringify(saved)); } catch {}
  }

  function setValue(handle, value, min, max) {
    handle.setAttribute('aria-valuemin', String(min));
    handle.setAttribute('aria-valuemax', String(max));
    handle.setAttribute('aria-valuenow', String(value));
  }

  function resizeColumns() {
    if (!grid.clientWidth || !get('search-divider').clientWidth) return;
    const min = 202;
    const max = Math.max(min, Math.min(460, grid.clientWidth - 530));
    const width = clamp(pixels(saved.search, 250), min, max);
    workspace.style.setProperty('--search-width', width + 'px');
    setValue(get('search-divider'), width, min, max);
    resizeJobs();
  }

  function resizeJobs() {
    if (!reading.clientWidth || !get('detail-divider').clientWidth) return;
    const available = reading.clientWidth - get('detail-divider').clientWidth;
    const min = Math.min(215, Math.round(available * .42));
    const max = Math.max(min, available - 285);
    const width = clamp(pixels(saved.jobs, Math.round(available * .4)), min, max);
    workspace.style.setProperty('--jobs-width', width + 'px');
    setValue(get('detail-divider'), width, min, max);
  }

  function resizeRound(height) {
    if (height <= 12) {
      round.open = false;
      workspace.style.removeProperty('--round-height');
      setValue(get('round-divider'), 0, 0, roundLimit());
      return 0;
    }
    round.open = true;
    const max = Math.min(roundLimit(), Math.max(13, roundBody.scrollHeight));
    const size = clamp(height, 13, max);
    workspace.style.setProperty('--round-height', size + 'px');
    setValue(get('round-divider'), size, 0, max);
    return size;
  }

  const roundLimit = () => Math.round(Math.min(160, window.innerHeight * .25));
  round.addEventListener('toggle', () => {
    if (!round.open) {
      setValue(get('round-divider'), 0, 0, roundLimit());
    } else {
      setValue(get('round-divider'), roundBody.clientHeight, 0, roundLimit());
    }
  });

  function bindDivider(id, key, axis, readSize, apply, reset) {
    const handle = get(id);
    const dragClass = axis === 'clientY' ? 'workspace-resizing-rows' : 'workspace-resizing';
    let drag = null;
    handle.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      drag = {id:event.pointerId, start:event[axis], size:readSize(), moved:false};
      handle.focus({preventScroll:true});
      handle.setPointerCapture(event.pointerId);
      document.body.classList.add(dragClass);
      handle.classList.add('dragging');
      event.preventDefault();
    });
    handle.addEventListener('pointermove', event => {
      if (drag?.id !== event.pointerId) return;
      const delta = event[axis] - drag.start;
      if (!drag.moved && Math.abs(delta) < 4) return;
      drag.moved = true;
      apply(drag.size + delta);
    });
    function finish(event) {
      if (drag?.id !== event.pointerId) return;
      if (drag.moved) persist();
      drag = null;
      document.body.classList.remove(dragClass);
      handle.classList.remove('dragging');
    }
    for (const event of ['pointerup','pointercancel','lostpointercapture']) handle.addEventListener(event, finish);
    handle.addEventListener('dblclick', () => {
      delete saved[key];
      reset();
      persist();
    });
    handle.addEventListener('keydown', event => {
      const positive = axis === 'clientY' ? 'ArrowDown' : 'ArrowRight';
      const negative = axis === 'clientY' ? 'ArrowUp' : 'ArrowLeft';
      if (![positive, negative, 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      if (event.key === 'Home') {
        delete saved[key];
        reset();
      } else {
        const step = event.key === 'End' ? 10000 : event.key === positive ? 24 : -24;
        apply(readSize() + step);
      }
      persist();
    });
  }

  bindDivider('search-divider', 'search', 'clientX', () => get('discovery-settings').offsetWidth,
    size => { saved.search = size; resizeColumns(); }, resizeColumns);
  bindDivider('detail-divider', 'jobs', 'clientX', () => get('discovery-list').offsetWidth,
    size => { saved.jobs = size; resizeJobs(); }, resizeJobs);
  bindDivider('round-divider', 'round', 'clientY', () => round.open ? roundBody.clientHeight : 0,
    height => { saved.round = resizeRound(height); }, () => resizeRound(0));

  const observer = new ResizeObserver(resizeColumns);
  observer.observe(grid);
  observer.observe(reading);
  setValue(get('round-divider'), 0, 0, roundLimit());
  if (pixels(saved.round, 0) > 12) {
    round.open = true;
    workspace.style.setProperty('--round-height', Math.min(saved.round, roundLimit()) + 'px');
  }
  window.addEventListener('resize', () => {
    if (round.open && Number.isFinite(saved.round)) resizeRound(Math.min(saved.round, roundLimit()));
  });
})();
