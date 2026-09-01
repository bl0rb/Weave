// @vitest-environment jsdom
//
// A scoped exception to this suite's otherwise jsdom-free setup (see
// vitest.config.ts's own docstring and chat-app.test.tsx for the existing
// precedent) — covers a real conditional-rendering rule (the SSO link
// must appear if and only if the server-computed `ssoLoginUrl` prop is
// non-null) that a pure-function unit test can't exercise.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { LoginForm } from '@/app/login/login-form';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn(), refresh: vi.fn() }),
}));

describe('LoginForm', () => {
  afterEach(() => cleanup());

  it('does not render an SSO link when ssoLoginUrl is null (OIDC not configured for this deployment)', () => {
    render(<LoginForm weaveLoginUrl={null} ssoLoginUrl={null} ssoError={null} />);

    expect(screen.queryByRole('link', { name: /SSO/i })).toBeNull();
    // The Personal-Token form itself is always present regardless.
    expect(screen.getByLabelText('Personal-API-Token')).not.toBeNull();
  });

  it('renders a link to the given ssoLoginUrl when SSO is configured', () => {
    render(<LoginForm weaveLoginUrl={null} ssoLoginUrl="http://localhost:8004/v1/auth/oidc/login?return_to=x" ssoError={null} />);

    const link = screen.getByRole('link', { name: /Mit SSO anmelden/i }) as HTMLAnchorElement;
    expect(link.getAttribute('href')).toBe('http://localhost:8004/v1/auth/oidc/login?return_to=x');
  });

  it('shows a German message for a known ssoError reason from a failed callback redirect', () => {
    render(<LoginForm weaveLoginUrl={null} ssoLoginUrl={null} ssoError="invalid_code" />);

    expect(screen.getByRole('alert').textContent).toMatch(/abgelaufen oder ungültig/);
  });

  it('shows a generic German fallback message for an unrecognised ssoError value', () => {
    render(<LoginForm weaveLoginUrl={null} ssoLoginUrl={null} ssoError="something_unexpected" />);

    expect(screen.getByRole('alert').textContent).toMatch(/fehlgeschlagen/);
  });

  it('shows no alert at all when there is no ssoError and the form has not been submitted yet', () => {
    render(<LoginForm weaveLoginUrl={null} ssoLoginUrl={null} ssoError={null} />);

    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('renders the federated Weave login as the primary action when it is configured', () => {
    render(
      <LoginForm
        weaveLoginUrl="http://localhost:8004/v1/auth/ingest/login?return_to=x"
        ssoLoginUrl={null}
        ssoError={null}
      />
    );

    const link = screen.getByRole('link', { name: /Mit Weave anmelden/i }) as HTMLAnchorElement;
    expect(link.getAttribute('href')).toBe('http://localhost:8004/v1/auth/ingest/login?return_to=x');
    // The token form stays available underneath -- scripts and anyone who
    // prefers it are not pushed through a browser redirect.
    expect(screen.getByLabelText('Personal-API-Token')).not.toBeNull();
  });

  it('leaves the keyboard on the primary button, not the token field, when both are offered', () => {
    render(
      <LoginForm weaveLoginUrl="http://localhost:8004/v1/auth/ingest/login" ssoLoginUrl={null} ssoError={null} />
    );

    expect(document.activeElement).not.toBe(screen.getByLabelText('Personal-API-Token'));
  });

  it('focuses the token field when it is the only way in', () => {
    render(<LoginForm weaveLoginUrl={null} ssoLoginUrl={null} ssoError={null} />);

    expect(document.activeElement).toBe(screen.getByLabelText('Personal-API-Token'));
  });
});
