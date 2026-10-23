/* Apply the saved palette before the page paints; no account data is stored. */
(() => {
  const key = 'scigblast-theme';
  const system = window.matchMedia('(prefers-color-scheme: dark)');
  let preference = null;
  try { preference = localStorage.getItem(key); } catch { /* Storage may be disabled. */ }
  const valid = value => value === 'dark' || value === 'light';
  if (!valid(preference)) preference = null;
  const sun = '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.4 1.4m11.2 11.2L19 19M5 19l1.4-1.4M17.6 6.4L19 5"/>';
  const moon = '<path d="M20.5 13.1A8.7 8.7 0 0 1 10.9 3.5 8.7 8.7 0 1 0 20.5 13.1Z"/>';
  function apply() {
    const dark = (preference || (system.matches ? 'dark' : 'light')) === 'dark';
    document.documentElement.classList.add('theme-changing');
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      button.setAttribute('aria-pressed', String(dark));
      button.setAttribute('aria-label', dark ? '切换到日间模式' : '切换到夜间模式');
      button.title = dark ? '切换到日间模式' : '切换到夜间模式';
      button.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${dark ? moon : sun}</svg><span>${dark ? '夜间' : '日间'}</span>`;
    });
    // Commit the entire palette together rather than transitioning each control.
    void document.documentElement.offsetWidth;
    requestAnimationFrame(() => document.documentElement.classList.remove('theme-changing'));
  }
  apply();
  document.addEventListener('DOMContentLoaded', apply, {once:true});
  document.addEventListener('click', event => {
    if (!event.target.closest('[data-theme-toggle]')) return;
    preference = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    try { localStorage.setItem(key, preference); } catch { /* Keep this tab usable without storage. */ }
    apply();
  });
  system.addEventListener('change', () => { if (!preference) apply(); });
  window.addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    preference = valid(event.newValue) ? event.newValue : null;
    apply();
  });
})();
