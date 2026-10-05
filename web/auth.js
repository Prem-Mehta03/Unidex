/* Sign-in screen. Loaded before app.js and chat.js.
 *
 * - Asks /api/me whether this server needs a login and whether you are signed in.
 * - Shows a full-screen sign-in card when you are not.
 * - Wraps window.fetch once so that ANY "401 login_required" answer from /api/...
 *   (for example when a session expires) brings the sign-in card back. That way
 *   app.js and chat.js do not each need their own 401 handling.
 */
(function () {
  'use strict';

  const MESSAGES = {
    domain: 'That Google account is not a college account. Please sign in with your college email.',
    failed: 'Sign-in did not complete. Please try again.',
    cancelled: 'Sign-in was cancelled.',
  };

  const $ = (id) => document.getElementById(id);
  let shown = false;

  function showLogin(message, domains) {
    shown = true;
    const screen = $('login-screen');
    if (!screen) return;
    const error = $('login-error');
    error.textContent = message || '';
    error.hidden = !message;
    /* After a refusal, offer to pick another account instead of repeating the same attempt. */
    const button = $('login-btn');
    button.textContent = message ? 'Use a different account' : 'Continue with Google';
    button.href = message ? '/auth/login?switch=1' : '/auth/login';
    if (domains && domains.length > 0) {
      $('login-domain').textContent = domains.map((d) => '@' + d).join(', ');
    }
    screen.hidden = false;
    document.body.classList.add('login-open');
  }

  function showUser(me) {
    const chip = $('user-chip');
    chip.textContent = me.name || me.email;
    chip.title = me.email;
    chip.hidden = false;
    $('signout').hidden = false;
  }

  const realFetch = window.fetch.bind(window);
  window.fetch = async function (input, init) {
    const response = await realFetch(input, init);
    const url = typeof input === 'string' ? input : input.url;
    if (response.status === 401 && /^\/api\//.test(url) && !shown) showLogin('', null);
    return response;
  };

  async function start() {
    try {
      const response = await realFetch('/api/me');
      if (!response.ok) return;
      const me = await response.json();
      if (!me.login_required) return;
      if (me.signed_in) return showUser(me);
      showLogin(MESSAGES[me.error] || '', me.allowed_domains);
    } catch (err) {
      /* Offline or server down: the other scripts show their own errors. */
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    $('signout').addEventListener('click', async () => {
      try {
        await realFetch('/auth/logout', { method: 'POST' });
      } finally {
        window.location.assign('/');
      }
    });
    start();
  });
})();
