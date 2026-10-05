/* Unidex chat: the "Chat" tab.
 *
 * The browser keeps the current form ("what I understood") and sends it back with
 * each message, so the server stays stateless. Text is always inserted with
 * textContent, never innerHTML.
 */
(function () {
  'use strict';

  const U = window.Unidex;
  const { el, icon, put, plural } = U;
  const $ = (id) => document.getElementById(id);

  const TRY_PROMPTS = [
    'OOP midsem papers and notes',
    'DD compre papers, last 3 years',
    'M3 quiz 2 solutions',
    'Show only 2022 solutions',
  ];
  const STACKS_PER_REVEAL = 3;
  const ESTIMATE_DELAY_MS = 250;
  const MAX_INPUT_ROWS = 6;

  const chat = {
    form: null, /* the latest interpretation from the server, sent back as "previous" */
    options: null,
    busy: false,
    greeted: false,
  };

  /* ---------- Server calls ---------- */

  async function postJson(path, body) {
    const response = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error('HTTP ' + response.status);
    return response.json();
  }

  async function loadOptions() {
    if (chat.options) return chat.options;
    const response = await fetch('/api/chat/options');
    if (!response.ok) throw new Error('HTTP ' + response.status);
    chat.options = await response.json();
    return chat.options;
  }

  /* ---------- Message bubbles ---------- */

  function scrollToEnd() {
    const log = $('chat-log');
    window.requestAnimationFrame(() => {
      log.lastElementChild && log.lastElementChild.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    });
  }

  function addUser(text) {
    const row = el('div', 'msg msg-user');
    row.append(el('div', 'bubble bubble-user', text));
    $('chat-log').append(row);
    scrollToEnd();
  }

  function addBot() {
    const row = el('div', 'msg msg-bot');
    const avatar = el('span', 'avatar', 'U');
    const body = el('div', 'bot-body');
    const name = el('div', 'bot-name', 'Unidex Assistant');
    const bubble = el('div', 'bubble bubble-bot');
    body.append(name, bubble);
    row.append(avatar, body);
    $('chat-log').append(row);
    scrollToEnd();
    return { row, bubble };
  }

  function addBotText(text) {
    const { row, bubble } = addBot();
    bubble.append(el('p', 'bubble-text', text));
    return { row, bubble };
  }

  function addTyping() {
    const { row, bubble } = addBot();
    bubble.classList.add('typing');
    bubble.append(el('span', 'dot'), el('span', 'dot'), el('span', 'dot'));
    bubble.setAttribute('aria-label', 'Unidex is thinking');
    return row;
  }

  function promptChips(prompts, onPick) {
    const row = el('div', 'chip-row prompt-row');
    for (const text of prompts) {
      const chip = el('button', 'chip example-chip', text);
      chip.type = 'button';
      chip.addEventListener('click', () => onPick(text));
      row.append(chip);
    }
    return row;
  }

  function greet() {
    if (chat.greeted) return;
    chat.greeted = true;
    const { bubble } = addBotText(
      'Hi! I find past papers, solutions, notes and slides in the department drives. ' +
        'Tell me the course, the exam and what you need, and I will check with you before searching.'
    );
    bubble.append(promptChips(TRY_PROMPTS.slice(0, 3), send));
  }

  /* ---------- Sending messages ---------- */

  function setBusy(busy) {
    chat.busy = busy;
    $('send').disabled = busy;
  }

  async function send(rawText) {
    const text = rawText.trim();
    if (!text || chat.busy) return;
    addUser(text);
    const input = $('chat-input');
    input.value = '';
    autosize();
    setBusy(true);
    const typing = addTyping();
    try {
      const reply = await postJson('/api/chat/message', { message: text, previous: chat.form });
      typing.remove();
      showReply(reply);
    } catch (err) {
      typing.remove();
      showError(() => send(text));
    } finally {
      setBusy(false);
      input.focus();
    }
  }

  function showError(retry) {
    const { bubble } = addBotText('Sorry, I could not reach the server.');
    const button = el('button', 'btn btn-soft', 'Try again');
    button.type = 'button';
    button.addEventListener('click', retry);
    bubble.append(button);
  }

  function showReply(reply) {
    if (reply.kind === 'confirm' || reply.kind === 'clarify') chat.form = reply.interpretation;
    const { bubble } = addBotText(reply.text);
    for (const note of reply.notes || []) bubble.append(el('p', 'bubble-note', note));
    if (reply.kind === 'clarify') {
      const chips = el('div', 'chip-row prompt-row');
      for (const option of reply.course_options) {
        const chip = el('button', 'chip example-chip', option.label);
        chip.type = 'button';
        chip.addEventListener('click', () => send(option.code));
        chips.append(chip);
      }
      bubble.append(chips);
    } else if (reply.kind === 'unclear') {
      bubble.append(promptChips(TRY_PROMPTS.slice(0, 2), send));
    } else if (reply.kind === 'confirm') {
      loadOptions()
        .then((options) => bubble.append(confirmCard(reply, options)))
        .catch(() => showError(() => showReply(reply)));
    }
    scrollToEnd();
  }

  /* ---------- The confirm card ---------- */

  function yearLabel(year) {
    return year + '-' + String((year + 1) % 100).padStart(2, '0');
  }

  function confirmCard(reply, options) {
    const form = JSON.parse(JSON.stringify(reply.interpretation));
    const card = el('section', 'confirm');
    card.setAttribute('aria-label', 'Does this look right?');

    const head = el('div', 'confirm-head');
    head.append(el('h3', 'confirm-title', 'Does this look right?'));
    head.append(el('p', 'confirm-sub', 'Change anything below, then search the drives.'));
    card.append(head);

    const body = el('div', 'confirm-body');
    card.append(body);

    const estimate = el('p', 'estimate', 'Counting files…');
    const searchButton = el('button', 'btn btn-primary', 'Search drives');
    searchButton.type = 'button';
    searchButton.prepend(icon('search'));

    let timer = null;
    function refreshEstimate() {
      window.clearTimeout(timer);
      timer = window.setTimeout(async () => {
        try {
          const data = await postJson('/api/chat/results', { interpretation: form });
          estimate.textContent = 'About ' + plural(data.total_files, 'file') +
            ' in ' + plural(data.total_stacks, 'stack');
        } catch (err) {
          estimate.textContent = '';
        }
      }, ESTIMATE_DELAY_MS);
    }
    function changed() {
      chat.form = form;
      renderBody();
      refreshEstimate();
    }

    function section(title) {
      const box = el('div', 'confirm-section');
      box.append(el('h4', 'confirm-label', title));
      return box;
    }

    function removableChip(text, onRemove, aria) {
      const chip = el('button', 'filter-chip');
      chip.type = 'button';
      chip.setAttribute('aria-label', aria);
      chip.append(document.createTextNode(text), icon('close'));
      chip.addEventListener('click', onRemove);
      return chip;
    }

    function courseSection() {
      const box = section('Course');
      const row = el('div', 'chip-row');
      const labelOf = (code) => (options.courses.find((c) => c.code === code) || { label: code }).label;
      for (const code of form.courses) {
        if (form.courses.length > 1) {
          row.append(removableChip(labelOf(code), () => {
            form.courses = form.courses.filter((c) => c !== code);
            changed();
          }, 'Remove course ' + code));
        } else {
          row.append(el('span', 'chip chip-exam', labelOf(code)));
        }
      }
      const select = document.createElement('select');
      select.className = 'select';
      select.setAttribute('aria-label', form.courses.length > 1 ? 'Add another course' : 'Change course');
      select.append(new Option(form.courses.length > 1 ? 'Add another course…' : 'Change course…', ''));
      for (const course of options.courses) {
        if (!form.courses.includes(course.code)) select.append(new Option(course.label, course.code));
      }
      select.addEventListener('change', () => {
        if (!select.value) return;
        form.courses = form.courses.length > 1 ? form.courses.concat(select.value) : [select.value];
        changed();
      });
      row.append(select);
      box.append(row);
      return box;
    }

    function examSection() {
      const box = section('Exam (for past papers)');
      const row = el('div', 'chip-row');
      for (const exam of options.exams) {
        const on = form.exam_types.includes(exam.value);
        const pill = el('button', 'pill-toggle' + (on ? ' on' : ''), exam.label);
        pill.type = 'button';
        pill.setAttribute('aria-pressed', String(on));
        pill.addEventListener('click', () => {
          form.exam_types = on
            ? form.exam_types.filter((e) => e !== exam.value)
            : form.exam_types.concat(exam.value);
          if (form.exam_types.length === 0) form.quiz_number = null;
          changed();
        });
        row.append(pill);
      }
      box.append(row);
      if (form.quiz_number) {
        const note = el('p', 'confirm-hint', 'Quiz / test number ' + form.quiz_number + ' ');
        const clear = el('button', 'link-btn', 'clear');
        clear.type = 'button';
        clear.addEventListener('click', () => {
          form.quiz_number = null;
          changed();
        });
        note.append(clear);
        box.append(note);
      } else if (form.exam_types.length === 0) {
        box.append(el('p', 'confirm-hint', 'None selected means any exam.'));
      }
      return box;
    }

    function yearSection() {
      const box = section('Years');
      const row = el('div', 'chip-row');
      if (form.recent_years) {
        row.append(removableChip('Last ' + plural(form.recent_years, 'year'), () => {
          form.recent_years = null;
          changed();
        }, 'Remove year filter'));
      }
      for (const year of form.years) {
        row.append(removableChip(yearLabel(year), () => {
          form.years = form.years.filter((y) => y !== year);
          changed();
        }, 'Remove year ' + yearLabel(year)));
      }
      if (!form.recent_years && form.years.length === 0) {
        row.append(el('span', 'confirm-hint', 'Any year. Say “last 3 years” or “2022” to narrow it.'));
      }
      box.append(row);
      return box;
    }

    function topicSection() {
      const box = section('Topics');
      const row = el('div', 'chip-row');
      for (const topic of form.topics) {
        row.append(removableChip(topic, () => {
          form.topics = form.topics.filter((t) => t !== topic);
          changed();
        }, 'Remove topic ' + topic));
      }
      const field = document.createElement('input');
      field.type = 'text';
      field.className = 'topic-input';
      field.maxLength = 60;
      field.placeholder = '+ Add topic';
      field.setAttribute('aria-label', 'Add a topic');
      field.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        const value = field.value.trim().toLowerCase();
        if (value && form.topics.length < 8 && !form.topics.includes(value)) {
          form.topics = form.topics.concat(value);
          changed();
        }
      });
      row.append(field);
      box.append(row);
      box.append(el('p', 'confirm-hint', 'Topics are matched against the names of notes and slides.'));
      return box;
    }

    function materialSection() {
      const box = section('What to include');
      const grid = el('div', 'material-grid');
      for (const material of options.materials) {
        const on = form.materials.includes(material.value);
        const label = el('label', 'material' + (on ? ' on' : ''));
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.checked = on;
        input.addEventListener('change', () => {
          form.materials = input.checked
            ? form.materials.concat(material.value)
            : form.materials.filter((m) => m !== material.value);
          changed();
        });
        label.append(input, el('span', '', material.label));
        grid.append(label);
      }
      box.append(grid);
      return box;
    }

    function renderBody() {
      const focusedTopic = document.activeElement && document.activeElement.classList.contains('topic-input');
      put(body, courseSection(), examSection(), yearSection(), topicSection(), materialSection());
      if (focusedTopic) {
        const field = body.querySelector('.topic-input');
        if (field) field.focus();
      }
    }

    const footer = el('div', 'confirm-footer');
    footer.append(estimate, searchButton);
    card.append(footer);
    renderBody();
    estimate.textContent = 'About ' + plural(reply.estimated_files, 'file');

    searchButton.addEventListener('click', () => runResults(form));
    return card;
  }

  /* ---------- Results inside the chat ---------- */

  async function runResults(form) {
    if (chat.busy) return;
    setBusy(true);
    const typing = addTyping();
    try {
      const data = await postJson('/api/chat/results', { interpretation: form });
      typing.remove();
      showResults(data);
    } catch (err) {
      typing.remove();
      showError(() => runResults(form));
    } finally {
      setBusy(false);
    }
  }

  function showResults(data) {
    const { bubble } = addBotText(
      data.total_files === 0
        ? 'I found nothing for that.'
        : 'Found ' + plural(data.total_files, 'file') + ' in ' + plural(data.total_stacks, 'stack') + '.'
    );
    for (const note of data.notes) bubble.append(el('p', 'bubble-note', note));
    if (data.total_files === 0) {
      bubble.append(el('p', 'bubble-note', 'You can change the form above, or tell me what to change.'));
      return;
    }
    const open = new Set(data.stacks.slice(0, 1).map((stack) => stack.key));
    const list = el('div', 'chat-stacks');
    const more = el('button', 'btn btn-soft', '');
    more.type = 'button';
    let shown = 0;
    function reveal() {
      const next = data.stacks.slice(shown, shown + STACKS_PER_REVEAL);
      for (const stack of next) list.append(U.stackView(stack, open));
      shown += next.length;
      const left = data.stacks.length - shown;
      more.hidden = left <= 0;
      more.textContent = 'Show ' + plural(left, 'more stack');
      scrollToEnd();
    }
    more.addEventListener('click', reveal);
    bubble.append(list, more);
    reveal();
    bubble.append(el('p', 'bubble-note', 'You can refine this, for example “show only 2022 solutions” or “also include slides”.'));
  }

  /* ---------- Composer ---------- */

  function autosize() {
    const input = $('chat-input');
    input.style.height = 'auto';
    const lineHeight = parseFloat(window.getComputedStyle(input).lineHeight) || 22;
    input.style.height = Math.min(input.scrollHeight, lineHeight * MAX_INPUT_ROWS) + 'px';
  }

  /* ---------- Views ---------- */

  function currentView() {
    if (window.location.hash === '#search') return 'search';
    if (window.location.hash === '#chat') return 'chat';
    return window.location.search ? 'search' : 'chat';
  }

  function applyView() {
    const view = currentView();
    document.body.dataset.view = view;
    $('search-view').hidden = view !== 'search';
    $('chat-view').hidden = view !== 'chat';
    for (const name of ['chat', 'search']) {
      const tab = $('tab-' + name);
      tab.classList.toggle('tab-active', view === name);
      if (view === name) tab.setAttribute('aria-current', 'page'); else tab.removeAttribute('aria-current');
    }
    if (view === 'chat') {
      greet();
      $('chat-input').focus({ preventScroll: true });
    }
  }

  function init() {
    const tryRow = $('try-row');
    for (const text of TRY_PROMPTS) {
      const chip = el('button', 'chip example-chip', text);
      chip.type = 'button';
      chip.addEventListener('click', () => send(text));
      tryRow.append(chip);
    }
    $('composer').addEventListener('submit', (event) => {
      event.preventDefault();
      send($('chat-input').value);
    });
    $('chat-input').addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        send($('chat-input').value);
      }
    });
    $('chat-input').addEventListener('input', autosize);
    window.addEventListener('hashchange', applyView);
    applyView();
  }

  init();
})();
