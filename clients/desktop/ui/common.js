// Shared UI helpers for the tray panel and the settings window. No framework, no build step.
export const { invoke, Channel } = window.__TAURI__.core;
export const { listen } = window.__TAURI__.event;

export const english = !navigator.language.toLowerCase().startsWith('de');

const EN = {
  'Weave einrichten': 'Set up Weave',
  'Tragen Sie die Adresse Ihres Weave-Servers ein.': 'Enter the address of your Weave server.',
  'Einstellungen öffnen': 'Open settings',
  'Bei Weave anmelden': 'Sign in to Weave',
  'Die Anmeldung läuft über das SSO Ihrer Organisation im Browser.': "You sign in through your organisation's SSO in the browser.",
  'Anmelden': 'Sign in',
  'Anmeldung im Browser läuft …': 'Signing in in the browser …',
  'Abbrechen': 'Cancel',
  'Fragen': 'Ask',
  'Suchen': 'Search',
  'Alle Wissensräume': 'All knowledge spaces',
  'Verlauf leeren': 'Clear history',
  'Im Web öffnen': 'Open on the web',
  'Frühere Fragen im Web': 'Earlier questions on the web',
  'Einstellungen': 'Settings',
  'Frage an Weave …': 'Ask Weave …',
  'In Weave suchen …': 'Search Weave …',
  'Senden': 'Send',
  'Stopp': 'Stop',
  'Kein Agent verfügbar': 'No agent available',
  'Was möchten Sie wissen?': 'What would you like to know?',
  'Ein Agent beantwortet Ihre Frage aus den freigegebenen Dokumenten – mit Quellen. Folgefragen bauen aufeinander auf.': 'An agent answers your question from the released documents – with sources. Follow-up questions build on each other.',
  'Zeigt passende Textstellen aus allen Wissensräumen, die Sie lesen dürfen – ohne KI-Antwort. Schneller, und Sie bewerten die Treffer selbst.': 'Shows matching passages from every knowledge space you may read – no AI answer. Faster, and you judge the hits yourself.',
  'Suchen statt fragen': 'Search instead of asking',
  'Fragen statt suchen': 'Ask instead of searching',
  'Keine Treffer.': 'No results.',
  'Öffnen': 'Open',
  'S. %@': 'p. %@',
  'Quellen': 'Sources',
  'Zusammenfassung': 'Summary',
  'Bereichs-Übersicht': 'Space overview',
  'Abgebrochen.': 'Stopped.',
  'Weave denkt nach …': 'Weave is thinking …',
  'Sitzung abgelaufen – bitte neu anmelden.': 'Session expired – please sign in again.',
  'Server': 'Server',
  'API-Adresse': 'API address',
  'Öffentliche Adresse von weave-api – dieselbe, auf die der Browser bei der Chat-Anmeldung weiterleitet (ohne /v1).': 'Public address of weave-api – the one the browser is sent to when signing in to the chat (without /v1).',
  'Verbindung testen': 'Test connection',
  'Erreichbar': 'Reachable',
  'Von der IT vorgegeben': 'Managed by IT',
  'Konto': 'Account',
  'Angemeldet als %@': 'Signed in as %@',
  'Angemeldet': 'Signed in',
  'Nicht angemeldet': 'Not signed in',
  'Abmelden': 'Sign out',
  'Bedienung': 'Usage',
  'Tastenkürzel': 'Hotkey',
  'Öffnet das Fragefenster von überall, z. B. CmdOrCtrl+Shift+Space. Leer = aus.': 'Opens the ask window from anywhere, e.g. CmdOrCtrl+Shift+Space. Empty = off.',
  'Standard-Agent': 'Default agent',
  'Erster verfügbarer': 'First available',
  'Nach der Anmeldung wählbar': 'Available after sign-in',
  'Beim Anmelden am Computer starten': 'Start when I log in to this computer',
  'Speichern': 'Save',
  'Einrichtung aus Weave-Chat': 'Setup from Weave Chat',
  'Ein Link aus Weave-Chat schlägt diesen Server vor. Nur übernehmen, wenn Sie den Link selbst geöffnet haben.': 'A link from Weave Chat proposes this server. Accept it only if you opened the link yourself.',
  'Ein Link aus Weave-Chat schlägt einen Server vor; die Adresse ist aber von der IT vorgegeben und bleibt unverändert.': 'A link from Weave Chat proposes a server, but the address is managed by IT and stays unchanged.',
  'Übernehmen': 'Accept',
  'Ablehnen': 'Dismiss',
  'Schließen': 'Close',
  'Gespeichert': 'Saved',
  'Weave fragen': 'Ask Weave',
  'Über Weave': 'About Weave',
  'Version': 'Version',
  'Nach Updates suchen': 'Check for updates',
  'Suche nach Updates …': 'Checking for updates …',
  'Weave ist aktuell.': 'Weave is up to date.',
  'Version %@ ist verfügbar.': 'Version %@ is available.',
  'Jetzt installieren': 'Install now',
  'Update wird installiert – Weave startet danach neu …': 'Installing the update – Weave restarts afterwards …',
  'Updates verteilt Ihre IT.': 'Your IT distributes updates.',
  'Update auf %@': 'Update to %@',
};

/** Translates a German source string and fills its `%@` placeholders in order. */
export function t(key, ...args) {
  const text = english ? EN[key] ?? key : key;
  let i = 0;
  return text.replace(/%@/g, () => (i < args.length ? String(args[i++]) : '%@'));
}

const PROPS = new Set(['value', 'checked', 'disabled', 'selected', 'hidden', 'required']);

export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props ?? {})) {
    if (value == null || value === false) continue;
    if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
    else if (key === 'class') el.className = value;
    else if (PROPS.has(key)) el[key] = value;
    else el.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

const ICONS = {
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  send: '<path d="M22 2 11 13M22 2l-7 20-4-9-9-4z"/>',
  arrowUp: '<path d="M12 19V5M5 12l7-7 7 7"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
  external: '<path d="M14 3h7v7M21 3l-9 9M19 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h5"/>',
  check: '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  warning: '<path d="M12 3 2 20h20z"/><path d="M12 9v5M12 17h.01"/>',
};

export function icon(name) {
  const span = document.createElement('span');
  span.innerHTML = `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;
  return span.firstChild;
}

export const spinner = () => h('span', { class: 'spinner', role: 'status' });

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}

function inline(text) {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a data-href="$2">$1</a>');
}

/** A small, safe Markdown subset: paragraphs, headings, lists, code, bold, italics, http(s) links. */
export function markdown(source) {
  const out = [];
  let list = null;
  let code = null;
  const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };
  for (const line of source.replace(/\r\n/g, '\n').split('\n')) {
    if (code !== null) {
      if (line.startsWith('```')) { out.push(`<pre>${escapeHtml(code.join('\n'))}</pre>`); code = null; }
      else code.push(line);
      continue;
    }
    if (line.startsWith('```')) { closeList(); code = []; continue; }
    const heading = line.match(/^(#{1,4})\s+(.*)/);
    const bullet = line.match(/^\s*[-*•]\s+(.*)/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)/);
    if (heading) { closeList(); out.push(`<h4>${inline(heading[2])}</h4>`); }
    else if (bullet || numbered) {
      const kind = bullet ? 'ul' : 'ol';
      if (list !== kind) { closeList(); out.push(`<${kind}>`); list = kind; }
      out.push(`<li>${inline((bullet ?? numbered)[1])}</li>`);
    } else if (!line.trim()) closeList();
    else { closeList(); out.push(`<p>${inline(line)}</p>`); }
  }
  if (code !== null) out.push(`<pre>${escapeHtml(code.join('\n'))}</pre>`);
  closeList();
  return out.join('');
}

/** Opens `data-href` links inside `root` in the default browser. */
export function wireLinks(root) {
  root.addEventListener('click', (event) => {
    const link = event.target.closest('a[data-href]');
    if (link) { event.preventDefault(); invoke('open_url', { url: link.dataset.href }); }
  });
}
