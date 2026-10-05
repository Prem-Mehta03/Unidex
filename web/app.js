/* Unidex front end: plain JavaScript, no build step.
 *
 * Everything shown comes from /api/search. Text is always inserted with
 * textContent (never innerHTML), so a strange file name cannot inject markup.
 */
(function () {
  'use strict';

  const API = '/api';
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const SEARCH_DELAY_MS = 300;
  const SUGGEST_DELAY_MS = 120;
  const MIN_SUGGEST_LENGTH = 2;
  const STACKS_OPEN_BY_DEFAULT = 2;
  const STACK_PAGE_SIZE = 10;
  const FACETS = [
    { key: 'course', title: 'Course' },
    { key: 'doc_type', title: 'Type of material' },
    { key: 'exam_type', title: 'Exam' },
    { key: 'year', title: 'Academic year' },
  ];
  const EXAMPLES = [
    'OOP midsem papers',
    'laplace transform notes',
    'DD compre solutions',
    'logic quiz',
  ];

  /* Icon shapes (24x24 grid, stroke only). Each item is [tag, attributes]. */
  const ICONS = {
    search: [['circle', { cx: 11, cy: 11, r: 8 }], ['path', { d: 'm21 21-4.3-4.3' }]],
    chevron: [['path', { d: 'm6 9 6 6 6-6' }]],
    external: [
      ['path', { d: 'M15 3h6v6' }],
      ['path', { d: 'M10 14 21 3' }],
      ['path', { d: 'M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6' }],
    ],
    close: [['path', { d: 'M18 6 6 18' }], ['path', { d: 'm6 6 12 12' }]],
    check: [['path', { d: 'M20 6 9 17l-5-5' }]],
    alert: [
      ['path', { d: 'm21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3' }],
      ['path', { d: 'M12 9v4' }],
      ['path', { d: 'M12 17h.01' }],
    ],
    file: [
      ['path', { d: 'M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z' }],
      ['path', { d: 'M14 2v6h6' }],
      ['path', { d: 'M16 13H8' }],
      ['path', { d: 'M16 17H8' }],
    ],
    layers: [
      ['path', { d: 'm12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z' }],
      ['path', { d: 'm22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65' }],
      ['path', { d: 'm22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65' }],
    ],
    sliders: [
      ['path', { d: 'M4 21v-7' }], ['path', { d: 'M4 10V3' }], ['path', { d: 'M12 21v-9' }],
      ['path', { d: 'M12 8V3' }], ['path', { d: 'M20 21v-5' }], ['path', { d: 'M20 12V3' }],
      ['path', { d: 'M2 14h4' }], ['path', { d: 'M10 8h4' }], ['path', { d: 'M18 16h4' }],
    ],
    moon: [['path', { d: 'M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z' }]],
    sun: [
      ['circle', { cx: 12, cy: 12, r: 4 }],
      ['path', { d: 'M12 2v2' }], ['path', { d: 'M12 20v2' }], ['path', { d: 'm4.93 4.93 1.41 1.41' }],
      ['path', { d: 'm17.66 17.66 1.41 1.41' }], ['path', { d: 'M2 12h2' }], ['path', { d: 'M20 12h2' }],
      ['path', { d: 'm6.34 17.66-1.41 1.41' }], ['path', { d: 'm19.07 4.93-1.41 1.41' }],
    ],
  };

  const state = {
    q: '',
    filters: { course: new Set(), doc_type: new Set(), exam_type: new Set(), year: new Set() },
    stacks: [],
    totalFiles: 0,
    totalStacks: 0,
    truncated: false,
    hasMore: false,
    facets: {},
    labels: { course: {}, doc_type: {}, exam_type: {}, year: {} },
    open: new Set(),
    requestId: 0,
    controller: null,
    loaded: false,
    detect: true,
    detected: [],
    suggestions: [],
    activeSuggestion: -1,
  };

  const $ = (id) => document.getElementById(id);
  const input = $('q');
  let searchTimer = null;
  let suggestTimer = null;
  let suggestSeq = 0; /* lets a slow, outdated suggestion reply be ignored */

  /* ---------- Small DOM helpers ---------- */

  function icon(name) {
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('class', 'icon');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('aria-hidden', 'true');
    for (const [tag, attrs] of ICONS[name]) {
      const shape = document.createElementNS(SVG_NS, tag);
      for (const [key, value] of Object.entries(attrs)) shape.setAttribute(key, String(value));
      svg.appendChild(shape);
    }
    return svg;
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== '') node.textContent = text;
    return node;
  }

  function put(parent, ...children) {
    parent.replaceChildren(...children);
  }

  function plural(count, word) {
    return count + ' ' + word + (count === 1 ? '' : 's');
  }

  /* ---------- State <-> URL ---------- */

  function activeFilterCount() {
    return FACETS.reduce((sum, f) => sum + state.filters[f.key].size, 0);
  }

  function hasQuery() {
    return state.q.trim() !== '' || activeFilterCount() > 0;
  }

  function readUrl() {
    const params = new URLSearchParams(window.location.search);
    state.q = (params.get('q') || '').slice(0, 200);
    for (const f of FACETS) state.filters[f.key] = new Set(params.getAll(f.key));
    state.detect = params.get('detect') !== '0';
    input.value = state.q;
  }

  function writeUrl(push) {
    const params = new URLSearchParams();
    if (state.q.trim()) params.set('q', state.q.trim());
    for (const f of FACETS) for (const value of state.filters[f.key]) params.append(f.key, value);
    if (!state.detect) params.set('detect', '0');
    const query = params.toString();
    const url = window.location.pathname + (query ? '?' + query : '') + window.location.hash;
    window.history[push ? 'pushState' : 'replaceState'](null, '', url);
  }

  function apiParams(offset) {
    const params = new URLSearchParams();
    if (state.q.trim()) params.set('q', state.q.trim());
    for (const f of FACETS) for (const value of state.filters[f.key]) params.append(f.key, value);
    if (!state.detect) params.set('detect', 'false');
    params.set('stack_offset', String(offset));
    params.set('stack_limit', String(STACK_PAGE_SIZE));
    return params;
  }

  /* ---------- Talking to the server ---------- */

  async function runSearch(append) {
    state.requestId += 1;
    const id = state.requestId;
    if (state.controller) state.controller.abort();
    state.controller = new AbortController();
    $('results').classList.add('loading');
    hideNotice();
    try {
      const offset = append ? state.stacks.length : 0;
      const response = await fetch(API + '/search?' + apiParams(offset), {
        signal: state.controller.signal,
      });
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const data = await response.json();
      if (id !== state.requestId) return;
      state.stacks = append ? state.stacks.concat(data.stacks) : data.stacks;
      state.totalFiles = data.total_files;
      state.totalStacks = data.total_stacks;
      state.truncated = data.truncated;
      state.hasMore = data.has_more;
      state.facets = data.facets;
      state.detected = data.detected_courses || [];
      state.loaded = true;
      for (const f of FACETS) {
        for (const option of data.facets[f.key] || []) state.labels[f.key][option.value] = option.label;
      }
      if (!append) {
        state.open = new Set(data.stacks.slice(0, STACKS_OPEN_BY_DEFAULT).map((s) => s.key));
      }
      render();
    } catch (err) {
      if (err.name === 'AbortError') return;
      showNotice('Could not reach the server. ', 'Try again', () => runSearch(append));
    } finally {
      if (id === state.requestId) $('results').classList.remove('loading');
    }
  }

  async function loadSuggestions() {
    const match = input.value.match(/([A-Za-z0-9]+)$/);
    if (!match || match[1].length < MIN_SUGGEST_LENGTH) return hideSuggestions();
    suggestSeq += 1;
    const seq = suggestSeq;
    try {
      const response = await fetch(API + '/suggest?prefix=' + encodeURIComponent(match[1]));
      if (!response.ok) return hideSuggestions();
      const data = await response.json();
      if (seq !== suggestSeq || !input.value.endsWith(match[1])) return;
      const typed = match[1].toLowerCase();
      state.suggestions = data.suggestions.filter((word) => word.toLowerCase() !== typed);
      state.activeSuggestion = -1;
      renderSuggestions();
    } catch (err) {
      hideSuggestions();
    }
  }

  async function loadHealth() {
    try {
      const response = await fetch(API + '/health');
      if (!response.ok) return;
      const data = await response.json();
      const pill = $('catalog-pill');
      pill.textContent = '● ' + data.documents.toLocaleString() + ' files indexed';
      pill.hidden = false;
    } catch (err) {
      /* The pill is decoration; ignore failures. */
    }
  }

  /* ---------- Rendering ---------- */

  function render() {
    renderSummary();
    renderChips();
    renderFacets();
    renderResults();
    const more = $('more');
    more.hidden = !state.hasMore;
    if (state.hasMore) {
      more.textContent = 'Show more stacks (' + (state.totalStacks - state.stacks.length) + ' remaining)';
    }
    const badge = $('filters-count');
    badge.textContent = String(activeFilterCount());
    badge.hidden = activeFilterCount() === 0;
  }

  function renderSummary() {
    const text = $('summary-text');
    if (!hasQuery() || !state.loaded) return put(text);
    if (state.totalFiles === 0) return put(text, document.createTextNode('No files found'));
    const parts = [
      el('strong', '', plural(state.totalFiles, 'file')),
      document.createTextNode(' found across ' + plural(state.totalStacks, 'stack')),
    ];
    if (state.truncated) {
      parts.push(document.createTextNode(' (only the best matches are shown)'));
    }
    put(text, ...parts);
  }

  function renderChips() {
    const row = $('active-chips');
    const chips = [];
    for (const course of state.detected) {
      const chip = el('button', 'filter-chip filter-chip-detected');
      chip.type = 'button';
      chip.title = 'Recognised from your search. Click to search the words as typed instead.';
      chip.setAttribute('aria-label', 'Stop limiting results to ' + course.label);
      chip.append(document.createTextNode('Course: ' + course.label), icon('close'));
      chip.addEventListener('click', () => {
        state.detect = false;
        writeUrl(true);
        runSearch(false);
      });
      chips.push(chip);
    }
    for (const f of FACETS) {
      for (const value of state.filters[f.key]) {
        const chip = el('button', 'filter-chip');
        chip.type = 'button';
        chip.setAttribute('aria-label', 'Remove filter ' + labelFor(f.key, value));
        chip.append(document.createTextNode(labelFor(f.key, value)), icon('close'));
        chip.addEventListener('click', () => toggleFilter(f.key, value, false));
        chips.push(chip);
      }
    }
    put(row, ...chips);
  }

  function labelFor(facet, value) {
    return state.labels[facet][value] || value;
  }

  function renderFacets() {
    const focused = document.activeElement;
    const focusKey = focused && focused.dataset && focused.dataset.facet
      ? focused.dataset.facet + '|' + focused.dataset.value
      : null;
    const groups = [];
    for (const f of FACETS) {
      const options = (state.facets[f.key] || []).map((o) => ({ ...o }));
      const present = new Set(options.map((o) => o.value));
      for (const value of state.filters[f.key]) {
        if (!present.has(value)) options.push({ value, label: labelFor(f.key, value), count: 0 });
      }
      if (options.length === 0) continue;
      const group = el('section', 'facet');
      group.append(el('h3', 'facet-title', f.title));
      const list = el('ul', 'facet-list');
      for (const option of options) list.append(optionRow(f.key, option));
      group.append(list);
      groups.push(group);
    }
    put($('facet-groups'), ...groups);
    if (focusKey) {
      for (const box of $('facet-groups').querySelectorAll('input')) {
        if (box.dataset.facet + '|' + box.dataset.value === focusKey) box.focus();
      }
    }
  }

  function optionRow(facet, option) {
    const row = el('li');
    const label = el('label', 'option' + (option.count === 0 ? ' option-empty' : ''));
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = state.filters[facet].has(option.value);
    box.dataset.facet = facet;
    box.dataset.value = option.value;
    box.addEventListener('change', () => toggleFilter(facet, option.value, box.checked));
    label.append(box, el('span', 'option-label', option.label), el('span', 'option-count', String(option.count)));
    row.append(label);
    return row;
  }

  function renderResults() {
    const area = $('results');
    if (!hasQuery()) return put(area, heroView());
    if (state.stacks.length === 0) return put(area, emptyView());
    put(area, ...state.stacks.map((stack) => stackView(stack)));
  }

  function heroView() {
    const hero = el('div', 'hero');
    hero.append(el('h1', '', 'What are you studying for?'));
    hero.append(el('p', '', 'Search past papers, solutions, notes and slides across the department drives. Every result opens the original file in Drive.'));
    const row = el('div', 'chip-row');
    for (const example of EXAMPLES) {
      const chip = el('button', 'chip example-chip', example);
      chip.type = 'button';
      chip.addEventListener('click', () => {
        input.value = example;
        state.detect = true;
        searchNow();
      });
      row.append(chip);
    }
    hero.append(row);
    return hero;
  }

  function emptyView() {
    const box = el('div', 'empty');
    box.append(el('strong', '', 'Nothing matched'));
    box.append(document.createTextNode(
      activeFilterCount() > 0
        ? 'Try removing a filter, or search for fewer words.'
        : 'Try different words, or a course nickname such as OOP, DD or M3.'
    ));
    return box;
  }

  function stackView(stack, openSet) {
    const opened = openSet || state.open;
    const open = opened.has(stack.key);
    const article = el('article', 'stack');
    article.dataset.open = String(open);

    const head = el('button', 'stack-head');
    head.type = 'button';
    head.setAttribute('aria-expanded', String(open));
    const iconBox = el('span', 'stack-icon');
    iconBox.append(icon(stack.kind === 'papers' ? 'file' : 'layers'));
    const main = el('span', 'stack-main');
    main.append(el('h2', 'stack-title', stack.title));
    const meta = el('span', 'stack-meta');
    meta.append(el('span', 'pill pill-muted', plural(stack.file_count, 'file')));
    if (stack.solution_count > 0) {
      meta.append(el('span', 'pill pill-ok', 'Includes ' + plural(stack.solution_count, 'solution')));
    }
    if (stack.year_span) meta.append(el('span', 'stack-years', stack.year_span));
    main.append(meta);
    const chevron = el('span', 'stack-chevron');
    chevron.append(icon('chevron'));
    head.append(iconBox, main, chevron);

    const body = el('div', 'stack-body');
    body.hidden = !open;
    body.append(...stack.files.map(cardView));

    head.addEventListener('click', () => {
      const nowOpen = !opened.has(stack.key);
      if (nowOpen) opened.add(stack.key); else opened.delete(stack.key);
      article.dataset.open = String(nowOpen);
      head.setAttribute('aria-expanded', String(nowOpen));
      body.hidden = !nowOpen;
    });
    article.append(head, body);
    return article;
  }

  function cardView(file) {
    const card = el('div', 'card');
    const chips = el('div', 'card-chips');
    if (file.course_code) chips.append(el('span', 'chip chip-code', file.course_code));
    if (file.exam_label) chips.append(el('span', 'chip chip-exam', file.exam_label));
    if (file.year_label) chips.append(el('span', 'chip', file.year_label));
    chips.append(el('span', 'chip', file.doc_type_label));
    if (file.has_solution && file.doc_type !== 'solution') chips.append(el('span', 'chip chip-ok', 'Has solution'));
    if (file.reviewed) chips.append(statusChip('chip chip-ok', 'check', 'Checked'));
    if (file.uncertain) chips.append(statusChip('chip chip-warn', 'alert', 'Tags unsure'));
    card.append(chips);
    card.append(el('h3', 'card-title', file.name));
    const where = [file.folder, file.instructor].filter(Boolean).join('  ·  ');
    card.append(el('p', 'card-path', where));
    if (file.why) card.append(el('p', 'card-why', file.why));

    const actions = el('div', 'card-actions');
    if (isWebUrl(file.url)) {
      const link = el('a', 'btn btn-primary');
      link.href = file.url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.append(document.createTextNode('Open in Drive'), icon('external'));
      actions.append(link);
    } else {
      actions.append(el('span', 'btn btn-disabled', 'No link available'));
    }
    if (file.uncertain) actions.append(el('span', 'hint', 'Automatic tags; double-check the year and exam.'));
    card.append(actions);
    return card;
  }

  function statusChip(className, iconName, text) {
    const chip = el('span', className);
    chip.append(icon(iconName), document.createTextNode(text));
    return chip;
  }

  function isWebUrl(url) {
    return typeof url === 'string' && /^https?:\/\//i.test(url);
  }

  function showNotice(text, actionLabel, action) {
    const box = $('notice');
    const button = el('button', 'btn btn-soft', actionLabel);
    button.type = 'button';
    button.addEventListener('click', action);
    box.replaceChildren(document.createTextNode(text), button);
    box.hidden = false;
  }

  function hideNotice() {
    $('notice').hidden = true;
  }

  /* ---------- Suggestions (autocomplete) ---------- */

  function renderSuggestions() {
    const list = $('suggestions');
    if (state.suggestions.length === 0) return hideSuggestions();
    const items = state.suggestions.map((word, index) => {
      const item = el('li', '', word);
      item.id = 'suggestion-' + index;
      item.setAttribute('role', 'option');
      item.setAttribute('aria-selected', String(index === state.activeSuggestion));
      item.addEventListener('mousedown', (event) => {
        event.preventDefault();
        applySuggestion(index);
      });
      return item;
    });
    put(list, ...items);
    list.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    if (state.activeSuggestion >= 0) {
      input.setAttribute('aria-activedescendant', 'suggestion-' + state.activeSuggestion);
    } else {
      input.removeAttribute('aria-activedescendant');
    }
  }

  function hideSuggestions() {
    suggestSeq += 1;
    state.suggestions = [];
    state.activeSuggestion = -1;
    $('suggestions').hidden = true;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
  }

  function applySuggestion(index) {
    const word = state.suggestions[index];
    if (!word) return;
    input.value = input.value.replace(/[A-Za-z0-9]+$/, word) + ' ';
    hideSuggestions();
    scheduleSearch();
    input.focus();
  }

  /* ---------- Actions ---------- */

  function toggleFilter(facet, value, on) {
    if (on) state.filters[facet].add(value); else state.filters[facet].delete(value);
    writeUrl(true);
    runSearch(false);
  }

  function clearFilters() {
    for (const f of FACETS) state.filters[f.key] = new Set();
    writeUrl(true);
    runSearch(false);
  }

  function scheduleSearch() {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      state.q = input.value;
      writeUrl(false);
      runSearch(false);
    }, SEARCH_DELAY_MS);
  }

  function searchNow() {
    window.clearTimeout(searchTimer);
    hideSuggestions();
    state.q = input.value;
    writeUrl(true);
    runSearch(false);
  }

  function setSheet(open) {
    document.body.classList.toggle('filters-open', open);
    $('scrim').hidden = !open;
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    const button = $('theme-toggle');
    put(button, icon(theme === 'dark' ? 'sun' : 'moon'));
    button.setAttribute('aria-label', theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme');
  }

  function toggleTheme() {
    const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    try {
      window.localStorage.setItem('unidex-theme', next);
    } catch (err) {
      /* Storage may be blocked; the theme still applies for this visit. */
    }
  }

  /* ---------- Wiring ---------- */

  function init() {
    $('search-icon').append(icon('search'));
    $('filters-icon').append(icon('sliders'));
    $('close-filters').append(icon('close'));
    applyTheme(document.documentElement.getAttribute('data-theme') || 'light');

    input.addEventListener('input', () => {
      state.detect = true;
      scheduleSearch();
      window.clearTimeout(suggestTimer);
      suggestTimer = window.setTimeout(loadSuggestions, SUGGEST_DELAY_MS);
    });
    input.addEventListener('keydown', (event) => {
      const count = state.suggestions.length;
      if (event.key === 'ArrowDown' && count) {
        event.preventDefault();
        state.activeSuggestion = (state.activeSuggestion + 1) % count;
        renderSuggestions();
      } else if (event.key === 'ArrowUp' && count) {
        event.preventDefault();
        state.activeSuggestion = (state.activeSuggestion - 1 + count) % count;
        renderSuggestions();
      } else if (event.key === 'Enter') {
        event.preventDefault();
        if (state.activeSuggestion >= 0) applySuggestion(state.activeSuggestion); else searchNow();
      } else if (event.key === 'Escape') {
        hideSuggestions();
      }
    });
    input.addEventListener('blur', () => window.setTimeout(hideSuggestions, 100));

    $('more').addEventListener('click', () => runSearch(true));
    $('clear-filters').addEventListener('click', clearFilters);
    $('theme-toggle').addEventListener('click', toggleTheme);
    $('open-filters').addEventListener('click', () => setSheet(true));
    $('close-filters').addEventListener('click', () => setSheet(false));
    $('scrim').addEventListener('click', () => setSheet(false));

    document.addEventListener('keydown', (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        input.focus();
        input.select();
      } else if (event.key === 'Escape') {
        setSheet(false);
      }
    });
    window.addEventListener('popstate', () => {
      readUrl();
      runSearch(false);
    });

    readUrl();
    loadHealth();
    runSearch(false);
  }

  /* Small toolkit shared with chat.js. */
  window.Unidex = { el, icon, put, plural, stackView, input, searchNow, state };

  init();
})();
