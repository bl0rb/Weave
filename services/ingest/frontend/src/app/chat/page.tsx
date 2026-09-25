import type { Metadata } from 'next';
import { resolveChatUrl } from '@/lib/chat-url';
import { getTranslator } from '@/i18n/server';

export const dynamic = 'force-dynamic';

export const metadata: Metadata = {
  title: 'Chat · Weave',
};

export default async function ChatPage() {
  const chatUrl = resolveChatUrl(process.env.WEAVE_CHAT_PUBLIC_URL);
  const { t } = await getTranslator();

  return (
    <main id="main-content" className="min-h-screen bg-slate-50 px-6 py-16 lg:px-12">
      <div className="mx-auto max-w-3xl">
        <p className="text-sm font-semibold uppercase tracking-[0.18em] text-emerald-700">Weave</p>
        <h1 className="mt-3 text-4xl font-semibold tracking-tight text-slate-950">{t('portal.chrome.breadcrumb.chat')}</h1>
        <p className="mt-5 max-w-2xl text-lg leading-8 text-slate-600">
          {t('portal.chatPage.description')}
        </p>
        {chatUrl ? (
          <a
            href={chatUrl}
            target="_blank"
            rel="noreferrer"
            className="mt-8 inline-flex rounded-xl bg-emerald-700 px-5 py-3 font-semibold text-white transition hover:bg-emerald-800 focus:outline-none focus:ring-2 focus:ring-emerald-600 focus:ring-offset-2"
          >
            {t('portal.chrome.openChat')}
          </a>
        ) : (
          <p className="mt-8 rounded-xl border border-amber-200 bg-amber-50 px-5 py-4 font-medium text-amber-900">
            {t('portal.chatPage.notConfigured')}
          </p>
        )}
      </div>
    </main>
  );
}
