// Chat app strings (rail, composer, scope picker, sources, messages, guard/
// trace panels, login). Flat keys, German source — see this catalog's own
// docstring in messages/index.ts for the `{name}`/`singular|plural` syntax.
export const chat = {
  // components/chat/chat-app.tsx
  'header.openRail': 'Gespräche anzeigen',
  'header.noBotSelected': 'Kein Bot ausgewählt',
  'header.releasedSourcesOnly': 'Nur freigegebene Quellen',
  'header.releasedSourcesOnlyTitle': 'Nur freigegebene, indexierte Dokumente, die du lesen darfst',
  'newConversation': 'Neues Gespräch',
  'confirm.deleteConversation': 'Diese Konversation und alle ihre Nachrichten unwiderruflich löschen?',
  'confirm.deleteAllConversations': 'Den gesamten Chat-Verlauf mit allen Nachrichten unwiderruflich löschen?',

  // components/chat/rail.tsx
  'rail.landmarkLabel': 'Gespräche',
  'rail.close': 'Gespräche schließen',
  'rail.historyLabel': 'Verlauf',
  'rail.clearHistory': 'Verlauf löschen',
  'rail.loading': 'Verlauf wird geladen…',
  'rail.empty': 'Noch keine gespeicherten Konversationen.',
  'rail.group.today': 'Heute',
  'rail.group.week': 'Diese Woche',
  'rail.group.older': 'Älter',
  'rail.untitled': 'Ohne Titel',
  'rail.deleteConversation': 'Konversation löschen',
  'rail.signedIn': 'Angemeldet',
  'rail.logout': 'Abmelden',

  // components/theme-toggle.tsx
  'theme.enableLight': 'Helles Farbschema aktivieren',
  'theme.enableDark': 'Dunkles Farbschema aktivieren',
  'theme.light': 'Helles Farbschema',
  'theme.dark': 'Dunkles Farbschema',

  // components/chat/composer.tsx
  'composer.placeholder': 'Nachricht schreiben…',
  'composer.placeholderNoBot': 'Bitte zuerst einen Bot auswählen',
  'composer.questionLabel': 'Deine Frage',
  'composer.assistantLabel': 'Assistent',
  'composer.noBotAvailable': 'Kein Bot verfügbar',
  'composer.send': 'Nachricht senden',
  'composer.sendHint': 'Enter zum Senden · Shift+Enter für einen Zeilenumbruch',

  // components/chat/scope-picker.tsx (+ lib/chat-types.ts's scopeLabel)
  'scope.srLabel': 'Wissensbereiche: ',
  'scope.popoverLabel': 'In welchen Wissensbereichen suchen?',
  'scope.allMine': 'Alle meine Bereiche',
  'scope.loading': 'Wissensbereiche werden geladen…',
  'scope.emptyReadable': 'Für dich sind keine Wissensbereiche lesbar.',
  'scope.public': '(öffentlich)',
  'scope.footerHint': 'Nur Bereiche, die du nutzen darfst.',
  'scope.all': 'Alle Bereiche',
  'scope.count': '{count} Bereich|{count} Bereiche',

  // components/chat/sources-panel.tsx
  'sourcesPanel.title': 'Quellen',
  'sourcesPanel.subtitle': 'Belege der ausgewählten Antwort',
  'sourcesPanel.emptyUnknown': 'Belege erscheinen hier, sobald eine Antwort auf Quellen zurückgreift.',
  'sourcesPanel.emptyNone': 'Diese Antwort stützt sich auf keine Quelle.',
  'sourcesPanel.trustTitle': 'Worauf der Assistent zugreift',
  'sourcesPanel.trustBody':
    'Nur freigegebene und indexierte Dokumente aus Bereichen, die du lesen darfst. Findet er nichts Passendes, sagt er das — statt zu raten.',
  'sourcesPanel.scopeDt': 'Bereiche',

  // Shared by source-cards.tsx and sources-panel.tsx (same Source fields,
  // laid out differently — see source-cards.tsx's own docstring).
  'sources.imagesLabel': 'Bilder aus den Quellen',
  'sources.documentFallback': 'Dokument {id}',
  'sources.score': 'Score {value}',
  'sources.collectionLabel': 'Collection: {collection}',
  'sources.noCollection': 'ohne Collection',
  'sources.page': 'S. {page}',
  'sources.pageRange': 'S. {start}–{end}',
  'sources.none': '–',

  // components/chat/source-cards.tsx
  'sourceCards.toggle': 'Belege ({count})',
  'sourceCards.sourceLabel': 'Quelle: {value}',

  // components/chat/message-list.tsx
  'messageList.empty': 'Noch keine Nachrichten — stelle unten deine erste Frage.',
  'messageList.landmarkLabel': 'Gespräch',

  // components/chat/message-bubble.tsx
  'messageBubble.defaultAssistantName': 'Assistent',
  'messageBubble.generating': 'Antwort wird erzeugt…',
  'messageBubble.stillWorking': 'Der Assistent arbeitet noch …',
  'messageBubble.viaFallback':
    'Nicht gestreamt — als Fallback über die nicht-streamende Schnittstelle beantwortet.',
  'messageBubble.sourcedBy': 'Belegt durch {count} Quelle|Belegt durch {count} Quellen',
  'messageBubble.noMatchingSource': 'Keine passende Quelle',
  'messageBubble.showInSourcesPanel': 'In Quellenleiste anzeigen',
  'messageBubble.status.complete': 'fertig',
  'messageBubble.status.partial': 'teilweise',
  'messageBubble.status.failed': 'fehlgeschlagen',

  // components/chat/guard-banner.tsx
  'guard.headline': 'Keine Belege gefunden.',
  'guard.reason.noContext': 'Es wurden keine passenden Belege in den durchsuchbaren Collections gefunden.',
  'guard.reason.noCollections':
    'Für diese Anfrage steht keine Collection zur Verfügung, die dieser Bot durchsuchen darf.',
  'guard.reason.filterExcludedAll':
    'Deine Auswahl der Wissensbereiche schließt alle Collections aus, die dieser Bot für dich durchsuchen dürfte. Auswahl aufheben, um wieder alles zu durchsuchen, was dir erlaubt ist.',
  'guard.reason.unknown': 'Die Antwort unten ist keine wissensbasierte Antwort.',
  'guard.resetAndRetry': 'Auswahl zurücksetzen & neu fragen',

  // components/chat/trace-panel.tsx
  'trace.toggle': 'Trace',
  'trace.intent': 'Intent: {value}',
  'trace.confidence': 'Konfidenz: {value}%',
  'trace.router': 'Router: {value}',
  'trace.needsRetrieval': 'Retrieval nötig: {value}',
  'trace.needsTool': 'Tool nötig: {value}',
  'trace.model': 'Modell: {value}',
  'trace.yes': 'ja',
  'trace.no': 'nein',
  'trace.retrieval.label': 'Retrieval: ',
  'trace.retrieval.hits': '{used}/{candidates} Treffer verwendet',
  'trace.retrieval.searched': 'durchsucht: {collections}',
  'trace.retrieval.noneSearched': 'keine Collection durchsucht',
  'trace.retrieval.ownFilter': 'eigener Filter: {value}',
  'trace.retrieval.filterMatchesNone': '(Auswahl passt auf keine Collection)',
  'trace.retrieval.none': 'Kein Retrieval-Aufruf für diesen Turn.',
  'trace.guard.label': 'Guard: ',
  'trace.guard.triggered': 'ausgelöst ({reason})',
  'trace.guard.unknownReason': 'unbekannter Grund',
  'trace.n8n.dropped': '{count} Quelle(n) verworfen.',
  'trace.n8n.droppedExplanation':
    'Der n8n-Flow dieses Bots hat Quellen außerhalb seines erlaubten Collection-Umfangs gemeldet; Weave hat sie verworfen, bevor sie diese Antwort erreichen konnten. Das deutet auf eine fehlerhafte oder kompromittierte n8n-Konfiguration hin.',
  'trace.n8n.label': 'n8n: ',
  'trace.n8n.noneDropped': 'keine Quellen außerhalb des erlaubten Umfangs verworfen',
  'trace.agent.label': 'Agent ({mode})',
  'trace.agent.subagent': '{id}: {status} · {searches} Suchen · {hits} Treffer',
  'trace.agent.followups': '{count} Folgerunde(n) · Budget {used}/{budget}',
  'trace.timing': '{key}: {ms} ms',

  // app/login/login-form.tsx
  'login.heading': 'Anmelden',
  'login.subtitleFederated':
    'Melde dich mit deinem Weave-Konto an — demselben, mit dem du dich auch bei Weave Ingest anmeldest.',
  'login.subtitleToken':
    'Melde dich mit deinem persönlichen Weave-API-Token an. Es wird ausschließlich serverseitig als httpOnly-Cookie gespeichert — nie im Browser-JavaScript.',
  'login.withWeave': 'Mit Weave anmelden',
  'login.orWithToken': 'oder mit einem Token',
  'login.or': 'oder',
  'login.tokenLabel': 'Personal-API-Token',
  'login.submit': 'Anmelden',
  'login.submitPending': 'Wird geprüft…',
  'login.withSso': 'Mit SSO anmelden',
  'login.genericFailure': 'Anmeldung fehlgeschlagen. Bitte versuche es erneut.',
  'login.networkFailure': 'Die Anmeldeseite konnte den Server nicht erreichen. Bitte versuche es erneut.',
  'login.ssoError.missingCode':
    'Die SSO-Anmeldung ist ohne Code vom Gateway zurückgekommen. Bitte versuche es erneut.',
  'login.ssoError.invalidCode': 'Der SSO-Anmeldevorgang ist abgelaufen oder ungültig. Bitte versuche es erneut.',
  'login.ssoError.gatewayUnreachable':
    'Weave-API war während der SSO-Anmeldung nicht erreichbar. Bitte versuche es in Kürze erneut.',
  'login.ssoError.default':
    'Die Anmeldung über SSO ist fehlgeschlagen. Bitte versuche es erneut oder melde dich mit deinem Personal-Token an.',
} as const;
