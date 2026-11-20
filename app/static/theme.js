(() => {
  const root = document.documentElement;
  const key = 'echo-careers-theme';
  try {
    if (localStorage.getItem(key) === 'light') root.dataset.theme = 'light';
  } catch {
    // The default remains available when browser storage is disabled.
  }

  document.addEventListener('DOMContentLoaded', () => {
    const controls = document.querySelector('#theme-switch');
    controls.querySelector('[value="' + root.dataset.theme + '"]').checked = true;
    controls.addEventListener('change', event => {
      const theme = event.target.value;
      root.dataset.theme = theme;
      try { localStorage.setItem(key, theme); } catch {}
    });
  });
})();
