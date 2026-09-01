import type { NextConfig } from 'next';

// Intentionally no `env` block: WEAVE_API_BASE_URL is read live, per request,
// only inside server-only code (src/lib/weave-api-server.ts, imported
// exclusively by Route Handlers under src/app/api/**) — never by a 'use
// client' component. Next's compiler would otherwise freeze any
// process.env.<KEY> listed in `env` to whatever value was present at
// `next build` time, project-wide; that would defeat setting the target
// gateway URL at container start (see the docker-compose/Helm deployment).
// Nothing here needs to reach the browser at all: every call the UI makes
// goes to this same Next.js server first (see the Route Handlers), so
// there is no client-side API base URL, no CORS, and no way for
// WEAVE_API_BASE_URL — or the personal token stored server-side — to leak
// into a bundle shipped to the browser.
const nextConfig: NextConfig = {};

export default nextConfig;
