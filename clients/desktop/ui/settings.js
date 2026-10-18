// Settings window: server, account, hotkey, default agent, autostart. Looks like KeyZapper.
import { invoke, listen, t, h, icon, spinner } from './common.js';

let state = null;
let bots = null;
let form = null;
let check = null; // { checking } | { ok } | { error }
let accountError = null;
let saveResult = null; // { ok } | { error }

async function refresh() {
  state = await invoke('get_state');
  if (!form) form = { server_url: state.server_url, hotkey: state.hotkey, default_agent: state.default_agent };
  if (state.server_managed) form.server_url = state.server_url;
  if (state.hotkey_managed) form.hotkey = state.hotkey;
  if (state.signed_in && bots === null) bots = await invoke('list_bots').catch(() => []);
  if (!state.signed_in) bots = null;
  render();
}

async function testConnection() {
  check = { checking: true };
  render();
  try { await invoke('test_connection', { serverUrl: form.server_url }); check = { ok: true }; }
  catch (error) { check = { error: String(error) }; }
  render();
}

async function save() {
  try {
    await invoke('save_settings', { serverUrl: form.server_url, hotkey: form.hotkey, defaultAgent: form.default_agent });
    saveResult = { ok: true };
  } catch (error) {
    saveResult = { error: String(error) };
  }
  refresh();
}

async function signIn() {
  accountError = null;
  try { await invoke('sign_in'); } catch (error) { accountError = String(error); }
  refresh();
}

function managedBadge(managed) {
  return managed && h('span', { class: 'badge managed' }, t('Von der IT vorgegeben'));
}

function field(label, control, note) {
  return [h('div', { class: 'field' }, h('label', null, label), control), note && h('div', { class: 'field-note muted' }, note)];
}

function render() {
  document.getElementById('toolbar').replaceChildren(
    h('img', { src: 'weave.svg', alt: '', width: 22, height: 22 }),
    h('h1', null, 'Weave'),
    h('span', { class: 'spacer' }));

  const server = h('section', { class: 'card' },
    h('div', { class: 'card-head' }, h('h2', null, t('Server'), managedBadge(state.server_managed))),
    field(t('API-Adresse'),
      h('input', { type: 'text', class: 'mono', value: form.server_url, disabled: state.server_managed, placeholder: 'https://weave.example.com/api', spellcheck: 'false',
        oninput: (e) => { form.server_url = e.target.value; check = null; } }),
      t('Adresse von weave-api, z. B. https://weave.example.com/api')),
    h('div', { class: 'actions' },
      h('button', { onclick: testConnection, disabled: check?.checking }, t('Verbindung testen')),
      check?.checking && spinner(),
      check?.ok && h('span', { class: 'green' }, icon('check'), ' ', t('Erreichbar')),
      check?.error && h('span', { class: 'error-text' }, check.error)));

  const account = h('section', { class: 'card' },
    h('div', { class: 'card-head' }, h('h2', null, t('Konto'))),
    state.signed_in
      ? h('div', { class: 'actions' },
        h('span', { class: 'chip ok' }, icon('user'), h('span', null, state.username ? t('Angemeldet als %@', state.username) : t('Angemeldet'))),
        h('span', { class: 'spacer' }),
        h('button', { class: 'danger', onclick: () => invoke('sign_out') }, t('Abmelden')))
      : h('div', { class: 'actions' },
        h('span', { class: 'chip missing' }, icon('warning'), h('span', null, t('Nicht angemeldet'))),
        h('span', { class: 'spacer' }),
        state.login_pending
          ? [spinner(), t('Anmeldung im Browser läuft …'), h('button', { onclick: () => invoke('cancel_sign_in') }, t('Abbrechen'))]
          : h('button', { class: 'primary', onclick: signIn, disabled: !state.server_url }, t('Anmelden'))),
    accountError && h('div', { class: 'error-text' }, accountError));

  const agentOptions = bots?.length
    ? [h('option', { value: '' }, t('Erster verfügbarer')), bots.map((b) => h('option', { value: b.id, selected: b.id === form.default_agent }, b.name ?? b.id))]
    : h('option', { value: form.default_agent }, t('Nach der Anmeldung wählbar'));
  const usage = h('section', { class: 'card' },
    h('div', { class: 'card-head' }, h('h2', null, t('Bedienung'), managedBadge(state.hotkey_managed))),
    field(t('Tastenkürzel'),
      h('input', { type: 'text', class: 'mono', value: form.hotkey, disabled: state.hotkey_managed, spellcheck: 'false', oninput: (e) => { form.hotkey = e.target.value; } }),
      t('Öffnet das Fragefenster von überall, z. B. CmdOrCtrl+Shift+Space. Leer = aus.')),
    field(t('Standard-Agent'),
      h('select', { disabled: !bots?.length, onchange: (e) => { form.default_agent = e.target.value; } }, agentOptions)),
    h('label', { class: 'switch' },
      h('input', { type: 'checkbox', checked: state.autostart, onchange: (e) => invoke('set_autostart', { enabled: e.target.checked }) }),
      t('Beim Anmelden am Computer starten')));

  const actions = h('div', { class: 'actions' },
    h('span', { class: 'spacer' }),
    saveResult?.ok && h('span', { class: 'green' }, t('Gespeichert')),
    saveResult?.error && h('span', { class: 'error-text' }, saveResult.error),
    h('button', { class: 'primary', onclick: save }, t('Speichern')));

  document.getElementById('main').replaceChildren(server, account, usage, actions,
    h('div', { class: 'footer' }, t('Weave für den Desktop – Version %@', state.version)));
}

listen('state-changed', () => refresh());
listen('login-result', (event) => { accountError = event.payload ?? null; refresh(); });
refresh();
