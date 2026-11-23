(() => {
  const material = document.querySelector('.hero-material');
  if (!material) return;

  // Keep fallback text paintable while the decorative typeface loads.
  document.fonts?.load('400 1em "Geist Pixel Circle"')
    .then(fonts => {
      if (fonts.length) document.documentElement.classList.add('hero-font-ready');
    })
    .catch(() => {});

  if (!('IntersectionObserver' in window)) return;
  const observer = new IntersectionObserver(([entry]) => {
    material.classList.toggle('terrain-in-view', entry.isIntersecting);
  });
  observer.observe(material);
})();
