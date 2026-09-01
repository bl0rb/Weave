// Test-only stand-in for the real `server-only` package (see
// vitest.config.ts's alias). The real package's default export condition
// unconditionally throws — it relies on Next's build resolving its
// "react-server" export condition to a no-op instead, which plain Node
// (and therefore Vitest) does not do on its own. This stub reproduces
// that no-op so route-handler/lib tests can import server-only modules
// without pulling in a Next.js build.
export {};
