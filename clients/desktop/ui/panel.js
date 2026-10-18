// Tray panel and quick overlay (quick.html, opened by the hotkey): ask an agent or search Weave.
import { invoke, Channel, listen, t, h, icon, spinner, markdown, wireLinks } from './common.js';

let state = null;
let loginError = null;
let bots = null;
let collections = [];
let mode = 'ask';
let agent = localStorage.getItem('agent') ?? '';
let collection = '';
/** Ask mode: [{ role: 'user', text } | { role: 'answer', text, status, sources, options, error, done }] */
let messages = [];
let conversationId = null;
let streaming = false;
/** Search mode: { query, results } | { query, loading } | { error } */
let searchView = null;
let draft = '';
let update = null;

const panel = document.getElementById('panel');
/** The Spotlight-like overlay: one bar, answers expand below it. */
const QUICK = document.body.classList.contains('quick');
wireLinks(panel);

async function refresh() {
  state = await invoke('get_state');
  update = await invoke('get_update_state');
  if (!state.signed_in) bots = null;
  else if (bots === null) await loadLists();
  render();
}

async function loadLists() {
  try {
    [bots, collections] = await Promise.all([invoke('list_bots'), invoke('list_collections').catch(() => [])]);
    const ids = bots.map((b) => b.id);
    if (!ids.includes(agent)) agent = ids.includes(state.default_agent) ? state.default_agent : ids[0] ?? '';
  } catch (error) {
    bots = null;
    if (error !== 'unauthorized') loginError = String(error);
  }
}

function setAgent(id) {
  if (id === agent) return;
  agent = id;
  localStorage.setItem('agent', id);
  newConversation();
}

function newConversation() {
  if (streaming) invoke('stop_answer');
  messages = [];
  conversationId = null;
  render();
}

// MARK: Actions

async function submit(text) {
  text = text.trim();
  if (!text || streaming) return;
  draft = '';
  if (mode === 'search') return runSearch(text);
  if (!agent) return;

  messages.push({ role: 'user', text });
  const answer = { role: 'answer', text: '', status: t('Weave denkt nach …'), sources: [], options: [] };
  messages.push(answer);
  streaming = true;
  render();

  const channel = new Channel();
  let pending = false;
  channel.onmessage = (event) => {
    switch (event.type) {
      case 'conversation': conversationId = event.id; break;
      case 'trace': answer.options = event.trace?.clarification?.options ?? []; break;
      case 'status': answer.status = event.message; break;
      case 'delta': answer.text += event.text ?? ''; answer.status = null; break;
      case 'sources': answer.sources = event.sources ?? []; break;
      case 'error': answer.error = event.detail ?? 'Fehler'; break;
      default: break;
    }
    if (!pending) { pending = true; requestAnimationFrame(() => { pending = false; renderMessages(); }); }
  };
  try {
    await invoke('ask', {
      botId: agent,
      conversationId,
      message: text,
      collections: collection ? [collection] : null,
      onEvent: channel,
    });
  } catch (error) {
    answer.error = error === 'unauthorized' ? t('Sitzung abgelaufen – bitte neu anmelden.') : String(error);
  }
  if (!answer.text && !answer.error) answer.error = t('Abgebrochen.');
  answer.status = null;
  answer.done = true;
  streaming = false;
  render();
}

async function runSearch(query) {
  searchView = { query, loading: true };
  render();
  try {
    const response = await invoke('search', { query, collection: collection || null });
    searchView = { query, results: response.results };
  } catch (error) {
    searchView = { query, error: error === 'unauthorized' ? t('Sitzung abgelaufen – bitte neu anmelden.') : String(error) };
  }
  render();
}

function stop() {
  invoke('stop_answer');
}

// MARK: Rendering

function sourceLabel(source) {
  const name = source.original_filename ?? source.document ?? source.document_id;
  const page = source.page ?? (source.page_start != null ? (source.page_end && source.page_end !== source.page_start ? `${source.page_start}-${source.page_end}` : source.page_start) : null);
  return page != null ? `${name} · ${t('S. %@', page)}` : name;
}

// ADR-0009: a knowledge-memory source is a condensed summary or a space
// overview, not a quoted passage -- marked so the two are not confused.
const MEMORY_KIND = { document: 'Zusammenfassung', collection: 'Bereichs-Übersicht' };

function sourceChip(source) {
  const label = sourceLabel(source);
  const kind = MEMORY_KIND[source.memory_kind];
  const title = kind ? `${t(kind)}: ${label}` : label;
  const content = kind ? [h('span', { class: 'source-kind' }, t(kind)), label] : [label];
  return source.source_url
    ? h('button', { class: 'source', title, onclick: () => invoke('open_url', { url: source.source_url }) }, ...content)
    : h('span', { class: 'source', title }, ...content);
}

function renderAnswer(message) {
  const text = h('div', { class: 'answer-text' });
  text.innerHTML = markdown(message.text);
  const seen = new Set();
  const sources = message.sources.filter((s) => {
    const key = s.document_id + (s.page_start ?? '') + (s.memory_kind ?? '');
    return !seen.has(key) && seen.add(key);
  });
  return h('div', { class: 'msg-answer' },
    message.status && h('div', { class: 'status-line' }, spinner(), message.status),
    message.text && text,
    message.options.length > 0 && message.done && h('div', { class: 'options' },
      message.options.map((option) => h('button', { onclick: () => submit(option.message) }, option.label))),
    sources.length > 0 && h('div', { class: 'sources', 'aria-label': t('Quellen') }, sources.map(sourceChip)),
    message.error && h('div', { class: 'error-text' }, message.error),
  );
}

function renderMessages() {
  const content = document.getElementById('content');
  if (!content) return;
  const atBottom = content.scrollHeight - content.scrollTop - content.clientHeight < 40;
  if (QUICK) {
    content.replaceChildren(...messages.map((m) => (m.role === 'user' ? h('div', { class: 'msg-user' }, m.text) : renderAnswer(m))));
    content.scrollTop = content.scrollHeight;
    fitWindow();
    return;
  }
  content.replaceChildren(...(messages.length
    ? messages.map((m) => (m.role === 'user' ? h('div', { class: 'msg-user' }, m.text) : renderAnswer(m)))
    : [h('div', { class: 'empty compact' },
      h('img', { src: 'weave.svg', alt: '' }),
      h('h2', null, t('Was möchten Sie wissen?')),
      h('p', null, t('Antworten kommen nur aus freigegebenen Dokumenten – mit Quellen.')))]));
  if (atBottom || streaming) content.scrollTop = content.scrollHeight;
}

function renderSearch(content) {
  if (!searchView) {
    content.append(h('div', { class: 'empty compact' }, icon('search'), h('p', null, t('Durchsuchen Sie alle Wissensräume, die Sie lesen dürfen.'))));
  } else if (searchView.loading) {
    content.append(h('div', { class: 'status-line' }, spinner(), searchView.query));
  } else if (searchView.error) {
    content.append(h('div', { class: 'error-text' }, searchView.error));
  } else if (!searchView.results.length) {
    content.append(h('div', { class: 'empty compact' }, h('p', null, t('Keine Treffer.'))));
  } else {
    for (const hit of searchView.results) {
      const space = collections.find((c) => c.slug === hit.source.collection)?.name ?? hit.source.collection;
      const meta = [space, hit.source.page && t('S. %@', hit.source.page)].filter(Boolean).join(' · ');
      content.append(h('div', { class: 'hit' },
        h('div', { class: 'hit-head' },
          h('span', { class: 'hit-title', title: hit.source.document }, hit.source.document),
          hit.source.source_url && h('button', { class: 'link', onclick: () => invoke('open_url', { url: hit.source.source_url }) }, icon('external'), t('Öffnen'))),
        meta && h('div', { class: 'muted small' }, meta),
        h('div', { class: 'hit-text' }, hit.text)));
    }
  }
}

function renderSignedOut() {
  if (!state.server_url) {
    return h('div', { class: 'empty compact' },
      h('img', { src: 'weave.svg', alt: '' }),
      h('h2', null, t('Weave einrichten')),
      h('p', null, t('Tragen Sie die Adresse Ihres Weave-Servers ein.')),
      h('button', { class: 'primary', onclick: () => invoke('open_settings') }, t('Einstellungen öffnen')));
  }
  return h('div', { class: 'empty compact' },
    h('img', { src: 'weave.svg', alt: '' }),
    h('h2', null, t('Bei Weave anmelden')),
    h('p', null, t('Die Anmeldung läuft über das SSO Ihrer Organisation im Browser.')),
    state.login_pending
      ? h('div', { class: 'line' }, spinner(), t('Anmeldung im Browser läuft …'), h('button', { onclick: () => invoke('cancel_sign_in') }, t('Abbrechen')))
      : h('button', { class: 'primary', onclick: signIn }, t('Anmelden')),
    loginError && h('div', { class: 'error-text' }, loginError),
    h('button', { class: 'link', onclick: () => invoke('open_settings') }, t('Einstellungen')));
}

async function signIn() {
  loginError = null;
  try { await invoke('sign_in'); } catch (error) { loginError = String(error); }
  refresh();
}

let fitPending = false;
/** The overlay window follows its content's height. */
function fitWindow() {
  if (!QUICK || fitPending) return;
  fitPending = true;
  requestAnimationFrame(() => {
    fitPending = false;
    invoke('fit_quick', { height: Math.ceil(panel.getBoundingClientRect().height) + 24 });
  });
}

function quickNote(title, detail, ...buttons) {
  return h('div', { class: 'quick-note' },
    h('div', { class: 'text' }, h('strong', null, title), detail && h('span', { class: 'muted small' }, detail)),
    buttons);
}

function renderQuick() {
  const input = h('textarea', {
    rows: 1,
    disabled: !state.signed_in,
    placeholder: mode === 'ask' ? t('Frage an Weave …') : t('In Weave suchen …'),
    value: draft,
    oninput: (e) => { draft = e.target.value; e.target.style.height = 'auto'; e.target.style.height = `${e.target.scrollHeight}px`; fitWindow(); },
    onkeydown: (e) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(e.target.value); }
    },
  });
  const bar = h('div', { class: 'quick-bar' },
    h('img', { src: 'weave.svg', alt: '' }),
    input,
    state.signed_in && mode === 'ask' && h('select', { 'aria-label': 'Agent', disabled: !bots?.length, onchange: (e) => setAgent(e.target.value) },
      bots?.length ? bots.map((b) => h('option', { value: b.id, selected: b.id === agent, title: b.description ?? '' }, b.name ?? b.id)) : h('option', null, t('Kein Agent verfügbar'))),
    state.signed_in && h('button', {
      class: 'icon-only plain', title: mode === 'ask' ? t('Suchen') : t('Fragen'), 'aria-label': mode === 'ask' ? t('Suchen') : t('Fragen'),
      onclick: () => { mode = mode === 'ask' ? 'search' : 'ask'; render(); },
    }, icon(mode === 'ask' ? 'search' : 'send')),
    state.signed_in && mode === 'ask' && messages.length > 0 && h('button', { class: 'icon-only plain', title: t('Neue Unterhaltung'), 'aria-label': t('Neue Unterhaltung'), onclick: newConversation }, icon('plus')),
    streaming
      ? h('button', { class: 'quick-send', onclick: stop, title: t('Stopp') }, icon('stop'))
      : h('button', { class: 'primary quick-send', title: t('Senden'), disabled: !state.signed_in || (mode === 'ask' && !agent), onclick: () => submit(input.value) }, icon('arrowUp')));

  const parts = [bar];
  if (!state.signed_in) {
    parts.push(!state.server_url
      ? quickNote(t('Weave einrichten'), t('Tragen Sie die Adresse Ihres Weave-Servers ein.'),
        h('button', { onclick: () => invoke('open_settings') }, t('Einstellungen öffnen')))
      : quickNote(t('Bei Weave anmelden'), loginError ?? t('Die Anmeldung läuft über das SSO Ihrer Organisation im Browser.'),
        state.login_pending
          ? [spinner(), h('button', { onclick: () => invoke('cancel_sign_in') }, t('Abbrechen'))]
          : [h('button', { class: 'primary', onclick: signIn }, t('Anmelden')), h('button', { onclick: () => invoke('open_settings') }, t('Einstellungen'))]));
  } else if (update?.available) {
    parts.push(quickNote(t('Version %@ ist verfügbar.', update.available), update.installing ? t('Update wird installiert – Weave startet danach neu …') : null,
      !update.installing && h('button', { onclick: () => invoke('install_update').catch(() => {}) }, t('Jetzt installieren'))));
  }
  const content = h('section', { class: 'quick-content', id: 'content' });
  if (state.signed_in && mode === 'ask' && messages.length) parts.push(content);
  if (state.signed_in && mode === 'search' && searchView) { parts.push(content); renderSearch(content); }
  panel.replaceChildren(...parts);
  if (content.isConnected && mode === 'ask') renderMessages();
  fitWindow();
  input.focus();
}

function render() {
  if (!state) return;
  if (QUICK) return renderQuick();
  const settingsButton = h('button', { class: 'icon-only plain', title: t('Einstellungen'), 'aria-label': t('Einstellungen'), onclick: () => invoke('open_settings') }, icon('gear'));
  if (!state.signed_in) {
    panel.replaceChildren(
      h('header', { class: 'panel-head' }, h('img', { src: 'weave.svg', alt: '' }), h('strong', null, 'Weave'), h('span', { class: 'spacer' }), settingsButton),
      renderSignedOut());
    return;
  }

  const agentSelect = h('select', { 'aria-label': 'Agent', disabled: mode === 'search' || !bots?.length, onchange: (e) => setAgent(e.target.value) },
    bots?.length ? bots.map((b) => h('option', { value: b.id, selected: b.id === agent, title: b.description ?? '' }, b.name ?? b.id)) : h('option', null, t('Kein Agent verfügbar')));
  const head = h('header', { class: 'panel-head' },
    h('img', { src: 'weave.svg', alt: '' }),
    agentSelect,
    h('span', { class: 'spacer' }),
    mode === 'ask' && h('button', { class: 'icon-only plain', title: t('Neue Unterhaltung'), 'aria-label': t('Neue Unterhaltung'), onclick: newConversation }, icon('plus')),
    settingsButton);

  const modeButton = (value, label) => h('button', { 'aria-pressed': String(mode === value), onclick: () => { mode = value; render(); } }, label);
  const tools = h('div', { class: 'panel-tools' },
    h('div', { class: 'segmented', role: 'group' }, modeButton('ask', t('Fragen')), modeButton('search', t('Suchen'))),
    collections.length > 0 && h('select', { 'aria-label': t('Alle Wissensräume'), onchange: (e) => { collection = e.target.value; } },
      h('option', { value: '' }, t('Alle Wissensräume')),
      collections.map((c) => h('option', { value: c.slug, selected: c.slug === collection }, c.name ?? c.slug))));

  const content = h('section', { class: 'content', id: 'content' });
  const updateBanner = update?.available && h('div', { class: 'update-banner' },
    update.installing ? [spinner(), t('Update wird installiert – Weave startet danach neu …')]
      : [h('span', null, t('Version %@ ist verfügbar.', update.available)),
        h('button', { class: 'link', onclick: () => invoke('install_update').catch(() => {}) }, t('Jetzt installieren'))]);
  const input = h('textarea', {
    rows: 1,
    placeholder: mode === 'ask' ? t('Frage an Weave …') : t('In Weave suchen …'),
    value: draft,
    oninput: (e) => { draft = e.target.value; e.target.style.height = 'auto'; e.target.style.height = `${e.target.scrollHeight}px`; },
    onkeydown: (e) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(e.target.value); }
    },
  });
  const sendButton = streaming
    ? h('button', { onclick: stop, title: t('Stopp') }, icon('stop'))
    : h('button', { class: 'primary', title: t('Senden'), disabled: mode === 'ask' && !agent, onclick: () => submit(input.value) }, icon(mode === 'ask' ? 'arrowUp' : 'search'));

  panel.replaceChildren(head, updateBanner || '', tools, content,
    h('footer', { class: 'composer' }, h('div', { class: 'composer-box' }, input, sendButton)));
  if (mode === 'ask') renderMessages(); else renderSearch(content);
  input.focus();
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { if (streaming) stop(); else invoke('hide_panel'); }
  if (QUICK && (e.metaKey || e.ctrlKey) && e.key === ',') { e.preventDefault(); invoke('open_settings'); }
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'n') { e.preventDefault(); newConversation(); }
});

listen('state-changed', () => refresh());
listen('update-changed', () => refresh());
listen('login-result', (event) => { loginError = event.payload ?? null; if (!loginError) bots = null; refresh(); });
listen('panel-shown', () => { refresh(); });
refresh();
