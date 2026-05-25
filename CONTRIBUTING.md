# Contributing to apidepth (Python package)

## Prerequisites

- Python >= 3.9

```bash
pip install -e ".[dev]"
```

## Running tests

```bash
pytest
```

## Making changes

**All changes to `main` must go through a pull request.** Direct pushes are blocked.

### Commit / PR title format

PR titles must follow [Conventional Commits](https://www.conventionalcommits.org/) — the title becomes the squash-merge commit message that drives automated versioning:

```
feat: add httpx instrumentation
fix: don't double-count retried requests
docs: add Django middleware example
chore: update dev dependencies
```

Use `feat!:` or put `BREAKING CHANGE: <description>` in the PR body for breaking changes (triggers a major version bump).

The `PR Title` check will fail and block merge if the format isn't followed.

## Release process

Releases are fully automated via [release-please](https://github.com/googleapis/release-please). You do not manually bump versions or tag releases.

1. **Merge your PR** — release-please reads the commit message and accumulates changes.
2. **A "Release PR" appears** — release-please opens a `chore: release X.Y.Z` PR that bumps `__version__` in `apidepth/version.py` and updates `CHANGELOG.md`. This PR stays open and updates itself as more commits land.
3. **Merge the Release PR** — triggers the publish job, which builds the distribution and uploads it to PyPI via OIDC trusted publisher.

### Version semantics

| Commit type | Version bump |
|---|---|
| `feat:` | minor |
| `fix:` | patch |
| `feat!:` or `BREAKING CHANGE` in body | major |
| `chore:`, `docs:`, `refactor:`, `test:` | no release |

### Do not edit `apidepth/version.py` manually

release-please owns that file. Manual edits will cause the manifest to drift and break the next automated release. Note that `pyproject.toml` uses `dynamic = ["version"]` — hatchling reads from `version.py` at build time; there is no version to edit in `pyproject.toml`.

## CI

Tests run on Python 3.9, 3.10, 3.11, 3.12, and 3.13. All five matrix jobs must pass before a PR can merge.

The test suite checks out fixtures from `apidepth-io/apidepth-collector` using the `GH_PAT` secret — this is pre-configured in the repo.

## Secrets / configuration (maintainers only)

| Secret / config | Details |
|---|---|
| `GH_PAT` | GitHub personal access token with `repo` scope |
| PyPI Trusted Publisher | Configured on PyPI (no GitHub secret needed). Go to pypi.org → your project → Publishing → add publisher: repo `apidepth-io/apidepth-python`, workflow `release-please.yml` |
