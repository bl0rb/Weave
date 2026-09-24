import type { chat as de } from '../de/chat';

export const chat: Record<keyof typeof de, string> = {
  // components/chat/chat-app.tsx
  'header.openRail': 'Show conversations',
  'header.noBotSelected': 'No assistant selected',
  'header.releasedSourcesOnly': 'Released sources only',
  'header.releasedSourcesOnlyTitle': 'Only released, indexed documents that you are allowed to read',
  'newConversation': 'New conversation',
  'confirm.deleteConversation': 'Permanently delete this conversation and all its messages?',
  'confirm.deleteAllConversations': 'Permanently delete the entire chat history and all its messages?',

  // components/chat/rail.tsx
  'rail.landmarkLabel': 'Conversations',
  'rail.close': 'Close conversations',
  'rail.historyLabel': 'History',
  'rail.clearHistory': 'Clear history',
  'rail.loading': 'Loading history…',
  'rail.empty': 'No saved conversations yet.',
  'rail.group.today': 'Today',
  'rail.group.week': 'This week',
  'rail.group.older': 'Older',
  'rail.untitled': 'Untitled',
  'rail.deleteConversation': 'Delete conversation',
  'rail.signedIn': 'Signed in',
  'rail.logout': 'Log out',

  // components/theme-toggle.tsx
  'theme.enableLight': 'Switch to light color scheme',
  'theme.enableDark': 'Switch to dark color scheme',
  'theme.light': 'Light color scheme',
  'theme.dark': 'Dark color scheme',

  // components/chat/composer.tsx
  'composer.placeholder': 'Write a message…',
  'composer.placeholderNoBot': 'Please select an assistant first',
  'composer.questionLabel': 'Your question',
  'composer.assistantLabel': 'Assistant',
  'composer.noBotAvailable': 'No assistant available',
  'composer.send': 'Send message',
  'composer.sendHint': 'Enter to send · Shift+Enter for a new line',

  // components/chat/scope-picker.tsx (+ lib/chat-types.ts's scopeLabel)
  'scope.srLabel': 'Knowledge spaces: ',
  'scope.popoverLabel': 'Which knowledge spaces to search?',
  'scope.allMine': 'All my spaces',
  'scope.loading': 'Loading knowledge spaces…',
  'scope.emptyReadable': 'No knowledge spaces are readable for you.',
  'scope.public': '(public)',
  'scope.footerHint': 'Only spaces you are allowed to use.',
  'scope.all': 'All spaces',
  'scope.count': '{count} space|{count} spaces',

  // components/chat/sources-panel.tsx
  'sourcesPanel.title': 'Sources',
  'sourcesPanel.subtitle': 'Evidence for the selected answer',
  'sourcesPanel.emptyUnknown': 'Sources will appear here as soon as an answer draws on any.',
  'sourcesPanel.emptyNone': 'This answer does not rely on any source.',
  'sourcesPanel.trustTitle': 'What the assistant can access',
  'sourcesPanel.trustBody':
    'Only released and indexed documents from spaces you are allowed to read. If it finds nothing suitable, it says so — instead of guessing.',
  'sourcesPanel.scopeDt': 'Spaces',

  // Shared by source-cards.tsx and sources-panel.tsx
  'sources.imagesLabel': 'Images from the sources',
  'sources.documentFallback': 'Document {id}',
  'sources.score': 'Score {value}',
  'sources.collectionLabel': 'Collection: {collection}',
  'sources.noCollection': 'no collection',
  'sources.page': 'p. {page}',
  'sources.pageRange': 'p. {start}–{end}',
  'sources.none': '–',

  // components/chat/source-cards.tsx
  'sourceCards.toggle': 'Sources ({count})',
  'sourceCards.sourceLabel': 'Source: {value}',

  // components/chat/message-list.tsx
  'messageList.empty': 'No messages yet — ask your first question below.',
  'messageList.landmarkLabel': 'Conversation',

  // components/chat/message-bubble.tsx
  'messageBubble.defaultAssistantName': 'Assistant',
  'messageBubble.generating': 'Generating answer…',
  'messageBubble.stillWorking': 'The assistant is still working …',
  'messageBubble.viaFallback': 'Not streamed — answered as a fallback through the non-streaming interface.',
  'messageBubble.sourcedBy': 'Backed by {count} source|Backed by {count} sources',
  'messageBubble.noMatchingSource': 'No matching source',
  'messageBubble.showInSourcesPanel': 'Show in sources panel',
  'messageBubble.status.complete': 'done',
  'messageBubble.status.partial': 'partial',
  'messageBubble.status.failed': 'failed',

  // components/chat/guard-banner.tsx
  'guard.headline': 'No evidence found.',
  'guard.reason.noContext': 'No matching evidence was found in the searchable collections.',
  'guard.reason.noCollections':
    'No collection is available for this request that this assistant is allowed to search.',
  'guard.reason.filterExcludedAll':
    'Your knowledge-space selection excludes every collection this assistant could otherwise search for you. Clear the selection to search everything you are allowed to again.',
  'guard.reason.unknown': 'The answer below is not a knowledge-based answer.',
  'guard.resetAndRetry': 'Reset selection & ask again',

  // components/chat/trace-panel.tsx
  'trace.toggle': 'Trace',
  'trace.intent': 'Intent: {value}',
  'trace.confidence': 'Confidence: {value}%',
  'trace.router': 'Router: {value}',
  'trace.needsRetrieval': 'Retrieval needed: {value}',
  'trace.needsTool': 'Tool needed: {value}',
  'trace.model': 'Model: {value}',
  'trace.yes': 'yes',
  'trace.no': 'no',
  'trace.retrieval.label': 'Retrieval: ',
  'trace.retrieval.hits': '{used}/{candidates} hits used',
  'trace.retrieval.searched': 'searched: {collections}',
  'trace.retrieval.noneSearched': 'no collection searched',
  'trace.retrieval.ownFilter': 'own filter: {value}',
  'trace.retrieval.filterMatchesNone': '(selection matches no collection)',
  'trace.retrieval.none': 'No retrieval call for this turn.',
  'trace.guard.label': 'Guard: ',
  'trace.guard.triggered': 'triggered ({reason})',
  'trace.guard.unknownReason': 'unknown reason',
  'trace.n8n.dropped': '{count} source(s) dropped.',
  'trace.n8n.droppedExplanation':
    "This assistant's n8n flow reported sources outside its allowed collection scope; Weave discarded them before they could reach this answer. This points to a misconfigured or compromised n8n setup.",
  'trace.n8n.label': 'n8n: ',
  'trace.n8n.noneDropped': 'no sources outside the allowed scope dropped',
  'trace.agent.label': 'Agent ({mode})',
  'trace.agent.subagent': '{id}: {status} · {searches} searches · {hits} hits',
  'trace.agent.followups': '{count} follow-up round(s) · budget {used}/{budget}',
  'trace.timing': '{key}: {ms} ms',

  // app/login/login-form.tsx
  'login.heading': 'Log in',
  'login.subtitleFederated': 'Sign in with your Weave account — the same one you use to sign in to Weave Ingest.',
  'login.subtitleToken':
    'Sign in with your personal Weave API token. It is stored exclusively server-side as an httpOnly cookie — never in browser JavaScript.',
  'login.withWeave': 'Log in with Weave',
  'login.orWithToken': 'or with a token',
  'login.or': 'or',
  'login.tokenLabel': 'Personal API token',
  'login.submit': 'Log in',
  'login.submitPending': 'Checking…',
  'login.withSso': 'Log in with SSO',
  'login.genericFailure': 'Login failed. Please try again.',
  'login.networkFailure': 'The login page could not reach the server. Please try again.',
  'login.ssoError.missingCode': 'The SSO login returned from the gateway without a code. Please try again.',
  'login.ssoError.invalidCode': 'The SSO login process has expired or is invalid. Please try again.',
  'login.ssoError.gatewayUnreachable': 'Weave API was unreachable during SSO login. Please try again shortly.',
  'login.ssoError.default':
    'Logging in via SSO failed. Please try again or sign in with your personal token instead.',
};
