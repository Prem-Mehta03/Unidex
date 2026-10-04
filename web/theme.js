/* Runs before the page paints so dark mode does not flash. */
(function () {
  'use strict';
  var stored = null;
  try {
    stored = window.localStorage.getItem('unidex-theme');
  } catch (err) {
    stored = null; /* storage can be blocked; fall back to the system setting */
  }
  var dark = stored ? stored === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
  document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
})();
