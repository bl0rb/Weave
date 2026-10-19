// App window (panel.html) and quick overlay (quick.html; tray click or hotkey): ask an agent or search Weave.
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
/** Set by submit(): renders scroll the new question up to the top, then the answer streams in below it untouched. */
let pinQuestion = false;
/** When the window was last shown or used -- after IDLE_RESET_MS away it opens on a fresh conversation. */
let lastActive = Date.now();
const IDLE_RESET_MS = 30 * 60 * 1000;

const panel = document.getElementById('panel');
document.documentElement.classList.add(navigator.userAgent.includes('Windows') ? 'os-windows' : 'os-mac');
/** The Spotlight-like overlay: one bar, answers expand below it. */
const QUICK = document.body.classList.contains('quick');
/** What the two modes do -- shown as tooltip on their switches and in search mode. */
const ASK_INFO = 'Ein Agent beantwortet Ihre Frage aus den freigegebenen Dokumenten – mit Quellen. Folgefragen bauen aufeinander auf.';
const SEARCH_INFO = 'Zeigt passende Textstellen aus allen Wissensräumen, die Sie lesen dürfen – ohne KI-Antwort. Schneller, und Sie bewerten die Treffer selbst.';
wireLinks(panel);

async function refresh(reloadLists = false) {
  state = await invoke('get_state');
  update = await invoke('get_update_state');
  if (!state.signed_in) bots = null;
  else if (bots === null || reloadLists) await loadLists();
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

  messages.push({ role: 'user', text, fresh: true });
  pinQuestion = true;
  lastActive = Date.now();
  const answer = { role: 'answer', text: '', status: t('Weave denkt nach …'), sources: [], options: [], fresh: true };
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
  lastActive = Date.now();
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
    message.status && h('div', { class: 'status-line' }, h('span', { class: 'shimmer-text' }, message.status)),
    message.text && text,
    message.options.length > 0 && message.done && h('div', { class: 'options' },
      message.options.map((option) => h('button', { onclick: () => submit(option.message) }, option.label))),
    sources.length > 0 && h('div', { class: 'sources', 'aria-label': t('Quellen') }, sources.map(sourceChip)),
    message.error && h('div', { class: 'error-text' }, message.error),
  );
}

/** A message fades in once, when it first appears -- not on every streamed delta. */
function renderMessage(m) {
  const el = m.role === 'user' ? h('div', { class: 'msg-user' }, m.text) : renderAnswer(m);
  if (m.fresh) { el.classList.add('appear'); m.fresh = false; }
  return el;
}

/** Busy: an answer is streaming or a search is running -- the logo turns. */
const isBusy = () => streaming || Boolean(searchView?.loading);
const logo = () => h('img', { src: 'weave.svg', alt: '', class: isBusy() ? 'logo busy' : 'logo' });

/** `previousScrollTop`: where the #content that a full render just replaced was scrolled to. */
function renderMessages(previousScrollTop) {
  const content = document.getElementById('content');
  if (!content) return;
  const scrollTop = previousScrollTop ?? content.scrollTop;
  if (QUICK) {
    content.replaceChildren(...messages.map(renderMessage));
  } else {
    content.replaceChildren(...(messages.length
      ? messages.map(renderMessage)
      : [h('div', { class: 'empty compact' },
        h('img', { src: 'weave.svg', alt: '' }),
        h('h2', null, t('Was möchten Sie wissen?')),
        h('p', null, t(ASK_INFO)),
        state.chat_url && h('button', { class: 'link', onclick: openWeb }, icon('external'), t('Frühere Fragen im Web')))]));
  }
  content.scrollTop = scrollTop;
  // Like the web chat: a new question moves up to the top (as far as the
  // answer below it allows), then the answer streams in without dragging
  // the view along to its end. Scrolling by hand ends this early.
  const question = pinQuestion && [...content.querySelectorAll('.msg-user')].pop();
  if (question) {
    const target = content.scrollTop + question.getBoundingClientRect().top - content.getBoundingClientRect().top - 8;
    content.scrollTop = target;
    if (content.scrollTop >= target - 1 || !streaming) pinQuestion = false;
  }
  if (QUICK) fitWindow();
}

/** The web chat -- on the open conversation, else on its history. */
function openWeb() {
  const url = conversationId ? `${state.chat_url}/?conversation=${encodeURIComponent(conversationId)}` : `${state.chat_url}/`;
  invoke('open_url', { url });
}

const webButton = () => state.chat_url && h('button', {
  class: 'icon-only plain',
  title: conversationId ? t('Im Web öffnen') : t('Frühere Fragen im Web'),
  'aria-label': conversationId ? t('Im Web öffnen') : t('Frühere Fragen im Web'),
  onclick: openWeb,
}, icon('external'));

const clearButton = () => h('button', { class: 'icon-only plain', title: t('Verlauf leeren'), 'aria-label': t('Verlauf leeren'), onclick: newConversation }, icon('trash'));

function renderSearch(content) {
  if (!searchView) {
    content.append(h('div', { class: 'empty compact' }, icon('search'), h('p', null, t(SEARCH_INFO))));
  } else if (searchView.loading) {
    for (let i = 0; i < 3; i++) {
      content.append(h('div', { class: 'hit skeleton', 'aria-hidden': 'true' },
        h('div', { class: 'skeleton-line', style: 'width: 45%' }),
        h('div', { class: 'skeleton-line', style: 'width: 92%' }),
        h('div', { class: 'skeleton-line', style: 'width: 70%' })));
    }
  } else if (searchView.error) {
    content.append(h('div', { class: 'error-text' }, searchView.error));
  } else if (!searchView.results.length) {
    content.append(h('div', { class: 'empty compact' }, h('p', null, t('Keine Treffer.'))));
  } else {
    for (const hit of searchView.results) {
      const space = collections.find((c) => c.slug === hit.source.collection)?.name ?? hit.source.collection;
      const meta = [space, hit.source.page && t('S. %@', hit.source.page)].filter(Boolean).join(' · ');
      content.append(h('div', { class: 'hit appear' },
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
    invoke('fit_quick', { height: Math.ceil(panel.getBoundingClientRect().height) });
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
    logo(),
    input,
    state.signed_in && mode === 'ask' && h('select', { 'aria-label': 'Agent', disabled: !bots?.length, onchange: (e) => setAgent(e.target.value) },
      bots?.length ? bots.map((b) => h('option', { value: b.id, selected: b.id === agent, title: b.description ?? '' }, b.name ?? b.id)) : h('option', null, t('Kein Agent verfügbar'))),
    state.signed_in && h('button', {
      class: 'icon-only plain',
      title: mode === 'ask' ? `${t('Suchen statt fragen')}: ${t(SEARCH_INFO)}` : `${t('Fragen statt suchen')}: ${t(ASK_INFO)}`,
      'aria-label': mode === 'ask' ? t('Suchen statt fragen') : t('Fragen statt suchen'),
      onclick: () => { mode = mode === 'ask' ? 'search' : 'ask'; render(); },
    }, icon(mode === 'ask' ? 'search' : 'send')),
    state.signed_in && mode === 'ask' && webButton(),
    state.signed_in && mode === 'ask' && messages.length > 0 && clearButton(),
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
  if (state.signed_in && mode === 'search' && !searchView) parts.push(quickNote(t('Suchen'), t(SEARCH_INFO)));
  const content = h('section', { class: 'quick-content', id: 'content', onwheel: () => { pinQuestion = false; } });
  const previousScrollTop = document.getElementById('content')?.scrollTop;
  if (state.signed_in && mode === 'ask' && messages.length) parts.push(content);
  if (state.signed_in && mode === 'search' && searchView) { parts.push(content); renderSearch(content); }
  panel.replaceChildren(...parts);
  if (content.isConnected && mode === 'ask') renderMessages(previousScrollTop);
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
    logo(),
    agentSelect,
    h('span', { class: 'spacer' }),
    mode === 'ask' && webButton(),
    mode === 'ask' && messages.length > 0 && clearButton(),
    settingsButton);

  const modeButton = (value, label, info) => h('button', { 'aria-pressed': String(mode === value), title: t(info), onclick: () => { mode = value; render(); } }, label);
  const tools = h('div', { class: 'panel-tools' },
    h('div', { class: 'segmented', role: 'group' }, modeButton('ask', t('Fragen'), ASK_INFO), modeButton('search', t('Suchen'), SEARCH_INFO)),
    collections.length > 0 && h('select', { 'aria-label': t('Alle Wissensräume'), onchange: (e) => { collection = e.target.value; } },
      h('option', { value: '' }, t('Alle Wissensräume')),
      collections.map((c) => h('option', { value: c.slug, selected: c.slug === collection }, c.name ?? c.slug))));

  const content = h('section', { class: 'content', id: 'content', onwheel: () => { pinQuestion = false; } });
  const previousScrollTop = document.getElementById('content')?.scrollTop;
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
  if (mode === 'ask') renderMessages(previousScrollTop); else renderSearch(content);
  input.focus();
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { if (streaming) stop(); else if (QUICK) invoke('hide_panel'); }
  if (QUICK && (e.metaKey || e.ctrlKey) && e.key === ',') { e.preventDefault(); invoke('open_settings'); }
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'n') { e.preventDefault(); newConversation(); }
});

listen('state-changed', () => refresh());
listen('update-changed', () => refresh());
listen('login-result', (event) => { loginError = event.payload ?? null; if (!loginError) bots = null; refresh(); });
// Shown again (tray icon, hotkey): fetch the agent list anew -- a bot added
// or shared since shows up -- and start fresh after a long time away.
listen('panel-shown', () => {
  if (!streaming && Date.now() - lastActive > IDLE_RESET_MS) { messages = []; conversationId = null; }
  lastActive = Date.now();
  refresh(true);
});
refresh();
