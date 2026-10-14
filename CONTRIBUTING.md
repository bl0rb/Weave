# Contributing

Thanks for considering a contribution. Weave is maintained by one person, so
a short exchange before a large change saves both of us time.

## Before you start

- **Security issues** do not belong in an issue or pull request. Follow
  [SECURITY.md](SECURITY.md).
- **Bugs and feature ideas:** open an issue using one of the templates.
- **Larger changes** — a new service, a contract change in `contracts/`, a
  database migration, or anything that touches authentication or
  permissions — please open an issue first and describe the approach.
  Architecture decisions are recorded as ADRs under `docs/adr/`.

## Making a change

1. Fork the repository and branch from `main`
   (`feat/<topic>`, `fix/<topic>`, `docs/<topic>`, `chore/<topic>`).
2. Keep the change focused. Unrelated refactoring, renaming, or reformatting
   makes a review harder and is usually asked to be split out.
3. Follow the conventions of the service you change. Each service under
   `services/` has its own dependencies, runtime, and test suite.
4. Configuration goes through `weave.yaml`. After changing a secret, an
   endpoint, or a service setting, run:

   ```bash
   python scripts/weave_config.py render
   python scripts/weave_config.py check
   ```

5. If you change an API, regenerate `contracts/openapi.json`. If you change
   behaviour that operators or users see, update the relevant guide in
   `docs/`.

## Tests

Run the suite of every service you touched. The commands are listed in the
README's [Testing](README.md#testing) section; `.github/workflows/pr-ci.yml`
is the executable reference. Pull-request CI runs only the suites whose paths
changed, so a passing CI run says nothing about a service you did not touch.

New behaviour comes with a test. A bug fix comes with a test that fails
without the fix.

## Commits and pull requests

- Commit messages follow Conventional Commits with the service as scope:
  `feat(ingest): …`, `fix(chat): …`, `docs(betrieb): …`.
- Fill in the pull request template: what changed, why, and how you tested
  it — including what you did *not* test.
- Dependabot and CI must be green before a merge.

## License

By contributing, you agree that your contribution is licensed under the
[MIT License](LICENSE) of this repository.
