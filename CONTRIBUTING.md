# Contributing to house_price_estimate

## Development setup

### Option 1: VS Code devcontainer (recommended)
Open the repository in VS Code and choose **Dev Containers: Reopen in Container**.
The container has Python 3.12, [uv](https://docs.astral.sh/uv/) and the
[GitHub CLI](https://cli.github.com/) (`gh`). On first start it creates the
environment (`.venv`) and installs the pre-commit hooks. Files are formatted on save.

The repository, including `data/`, is mounted into the container, so the downloads and the
built database are shared with the host.

#### Claude Code in the devcontainer
The Claude Code VS Code extension is installed in the container automatically (it is listed in
`devcontainer.json`). Your login, sessions
and memory live in a Docker named volume (`claude-code-config-<id>`, one per clone) mounted at
`/home/vscode/.claude`, so they survive container rebuilds. Log in once after the first build; you will
not be asked again.

The volume is stored by Docker, not in the repository, and no host paths are committed. Put personal
Claude settings in `.claude/settings.local.json` (gitignored); project-wide ones go in
`.claude/settings.json` (committed).

To log out completely or start fresh, stop the container, find the volume with
`docker volume ls | grep claude-code-config` and remove it with `docker volume rm <name>`. To share one
login across all your devcontainers, change the volume name in `devcontainer.json` to a fixed name such as
`claude-code-config` locally, but don't commit that change.

#### GitHub CLI in the devcontainer
Log in once inside the container:
```
gh auth login        # GitHub.com, HTTPS, then log in with a web browser
gh auth status       # check
```
`gh` keeps its login in `~/.config/gh`. That directory is a Docker named volume (`gh-config-<id>`, one
per clone), so the login survives container restarts and rebuilds. The container has no system keyring,
so the token is stored in plain text in `~/.config/gh/hosts.yml` inside the volume. Nothing is written to the
repository.

`git push` and `git pull` over HTTPS go through VS Code's credential forwarding from the host. If your host
gitconfig sets `gh auth git-credential` as the GitHub credential helper, VS Code copies that setting into the
container and git uses the container's `gh` login. To make git use `gh` in the container even if your host
doesn't, run `gh auth setup-git` once after logging in.

To log out, run `gh auth logout`. To start fresh, remove the volume as described for Claude Code above
(`docker volume ls | grep gh-config`).

### Option 2: Local
Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then from the repository root
```
uv sync                          # create .venv with house_price_estimate and the dev tools
uv run pre-commit install        # run checks on each commit
git config blame.ignoreRevsFile .git-blame-ignore-revs
```

The performance notebook needs extra packages: `uv sync --group notebook` (see the README).

### Everyday commands
| Task | Command |
|---|---|
| Run the CLI | `uv run house-price-estimate ...` (or `uv run python -m house_price_estimate ...`) |
| Run tests | `uv run pytest` |
| Tests with coverage | `uv run pytest --cov` (add `--cov-report=html` for a browsable report in `htmlcov/`) |
| Lint (and fix what can be fixed) | `uv run ruff check --fix .` |
| Format | `uv run ruff format .` |
| Run all pre-commit checks | `uv run pre-commit run --all-files` |
| Add a dependency | `uv add <package>` (dev tools: `uv add --group dev <package>`) |

Always change dependencies with `uv add` / `uv remove` so that `pyproject.toml` and `uv.lock` stay in sync, and
commit both.

## Code style
Style is enforced by [ruff](https://docs.astral.sh/ruff/), configured in `pyproject.toml`:
PEP 8 with an 88 character line length, double quotes, sorted imports, modern type hints and
Google-convention docstrings. The formatter is the final word on layout, so there is no need to
format by hand. `notebooks/` is excluded from ruff, because notebook code should only change
together with re-running the notebook. More detailed conventions are written down in
[CLAUDE.md](CLAUDE.md), which applies to people as well as AI agents.

## Pre-commit hooks
On each `git commit` the hooks:
- fix whitespace and file endings, and check YAML/TOML files,
- run `ruff check --fix` and `ruff format`,
- run the fast tests for the Python files being committed.

If a hook modifies a file, the commit stops: review the changes, `git add` them and commit again.

The test hook maps changed files to tests with `MODULE_TESTS` in `scripts/pytest_changed.py`, because
several modules share a test file (e.g. `match.py` is tested in `tests/test_address.py`, and `build.py`,
`cli.py` and `backtest.py` in `tests/test_estimate.py`). Other modules `house_price_estimate/<module>.py`
run `tests/test_<module>.py` if it exists. A changed test file runs itself, and `tests/conftest.py` runs
everything. Tests marked `slow` or `local_data` are not run by the hook. When you add a module or a test
file, update `MODULE_TESTS` if the names don't match. CI always runs the full suite.

In an emergency `git commit --no-verify` skips the hooks; CI will still check the change.

## Tests
- Tests live in `tests/test_<area>.py`: `test_address.py` (parsing and matching),
  `test_estimate.py` (build, estimation, backtest, calibration and CLI) and `test_offline.py`
  (network guards).
- The session fixture `paths` in `tests/conftest.py` writes a small synthetic dataset in the real raw file
  formats and builds a database from it once per test session. Extend it rather than reading the real
  downloads. Small committed input files go in `tests/data/`, expected outputs in
  `tests/expected/<test_name>/`.
- Mark tests that need extra resources, so they skip cleanly elsewhere:
  - `@pytest.mark.local_data`: needs large data that is not in the repository. These run only when
    `HOUSE_PRICE_ESTIMATE_LOCAL_DATA` points to a directory with that data
    (`HOUSE_PRICE_ESTIMATE_LOCAL_DATA=/path/to/data uv run pytest`).
  - `@pytest.mark.slow`: takes more than a couple of seconds.
- CI fails if total coverage drops below the `fail_under` value in `pyproject.toml`. Raise it as coverage
  improves.

## Branching
We use [GitHub Flow](https://docs.github.com/en/get-started/using-github/github-flow):

1. `main` is always in a working, releasable state. Don't commit to it directly.
2. Create a short-lived branch from `main` for each piece of work, named by type:
   `feat/<topic>`, `fix/<topic>`, `docs/<topic>`, `chore/<topic>`.
3. Open a pull request early (a draft PR is fine). CI runs lint and tests on every push.
4. Get one review from another contributor. Leave a comment or approval on the PR so there is a record.
5. **Squash and merge**. The PR title becomes the single commit on `main`, so it must be a Conventional
   Commit (checked by CI). Delete the branch afterwards.

There are no `develop` or long-lived release branches. Urgent fixes are just a `fix:` PR.

## Commit messages and PR titles
Follow [Conventional Commits](https://www.conventionalcommits.org/): `type(scope): summary`.

| Type | Use for | Version bump (before 1.0) |
|---|---|---|
| `feat` | New functionality | minor (0.**x**.0) |
| `fix` | Bug fixes | patch (0.0.**x**) |
| `perf` | Performance improvements | patch |
| `docs`, `test`, `refactor`, `build`, `ci`, `chore` | Everything else | none |

The scope is usually the subcommand or module. Mark breaking changes with `!` (`feat!: ...`) or a
`BREAKING CHANGE:` footer.

Commits on feature branches can be informal, because only the squashed PR title lands on `main`.

## Versioning and releases
house_price_estimate follows [Semantic Versioning](https://semver.org/). While the version is `0.x`, breaking changes
bump the minor version.

Releases are automated with [release-please](https://github.com/googleapis/release-please):

1. After each merge to `main`, release-please updates an open PR titled `chore(main): release X.Y.Z`.
   It contains the next version number (from the commit types since the last release) and the
   `CHANGELOG.md` entries.
2. When you want to release, merge that PR. Several features can be batched into one release.
3. Merging tags `vX.Y.Z`, creates a GitHub Release with the changelog, and attaches the built wheel.

Never edit the version in `pyproject.toml` or `CHANGELOG.md` by hand; release-please owns them.
Users install a release with `pip install git+https://github.com/falklab/house_price_estimate.git@vX.Y.Z`.

## Repository settings (admin, one-off)
- **Settings → General → Pull Requests**: allow squash merging only, with the default commit message set to
  "Pull request title"; enable "Automatically delete head branches".
- **Settings → Actions → General → Workflow permissions**: enable "Allow GitHub Actions to create and approve
  pull requests" (needed by release-please).
- **Settings → Rules → Rulesets** (requires a paid GitHub plan for private repositories): protect `main`,
  require a pull request, and require the `lint`, `test` and `conventional-title` checks.
- Release PRs are opened by the Actions bot, so CI does not run on them. This is fine, because they only
  change the version and changelog.
