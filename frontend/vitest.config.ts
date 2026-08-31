import path from 'node:path';
import { defineConfig } from 'vitest/config';
import tsconfigPaths from 'vite-tsconfig-paths';

// Deliberately small by default: no global jsdom, no next/jest. Almost
// everything under test here — the SSE parser, the error-mapping table, the
// Route Handlers — is plain TypeScript / Web Fetch API (Request/Response/
// ReadableStream, all present in Node's own runtime), so a plain Node
// environment is enough, and UI components are otherwise exercised by hand
// in the browser rather than through component tests: the actual risk in
// this codebase is mostly the token/cookie boundary and the SSE state
// machine, not JSX rendering. The one exception is chat-app.test.tsx, which
// opts itself into `jsdom` + React Testing Library via a per-file
// `// @vitest-environment jsdom` pragma to cover a genuine cross-handler
// state bug (see that file) — narrow enough not to justify moving the
// whole suite onto jsdom by default.
export default defineConfig({
  plugins: [tsconfigPaths()],
  resolve: {
    alias: {
      // Next's build resolves this package's "react-server" export
      // condition to a no-op; plain Node (and so Vitest) resolves its
      // default condition instead, which unconditionally throws. Swap in
      // a real no-op so importing a `server-only`-guarded module (every
      // Route Handler's dependencies) doesn't require running an actual
      // Next.js build. See src/test-stubs/server-only.ts.
      'server-only': path.resolve(__dirname, 'src/test-stubs/server-only.ts'),
    },
  },
  test: {
    environment: 'node',
    // `.test.tsx` is included alongside `.test.ts` for the one component
    // test in this suite (chat-app.test.tsx) that needs actual DOM
    // rendering — see that file's own docstring for why it, specifically,
    // opts out of the "no jsdom" default below via a per-file
    // `// @vitest-environment jsdom` pragma instead of changing this
    // default for the whole suite.
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
  },
});
