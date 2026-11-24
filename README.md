# Holiday planner

A small, responsive web application for keeping track of trips. Users sign in
with GitHub and see only their own trips. Trip data is stored in SQLite on the
server. This is a starter, not a production-ready public deployment.

## Run locally

Requires Python 3.12 or later and a GitHub OAuth app. In GitHub's developer
settings, create an OAuth app with homepage `http://127.0.0.1:5001` and
callback URL `http://127.0.0.1:5001/auth/callback`. Do not commit credentials.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[deploy]'
export GITHUB_CLIENT_ID='your-client-id'
export GITHUB_CLIENT_SECRET='your-client-secret'
./scripts/run.sh
```

`./scripts/run.sh` defaults to `HOST=127.0.0.1`, `PORT=5001`, `COOKIE_SECURE=0`,
`TRUST_PROXY=0`, and `ALLOWED_GITHUB_IDS=353556` (`@Lloydshove`), and generates
an ephemeral `SECRET_KEY` if unset. Open <http://127.0.0.1:5001>.

Alternatively, you can run Flask's dev server directly (for example on port
`5000` with `OAUTH_REDIRECT_URI='http://127.0.0.1:5000/auth/callback'`,
`COOKIE_SECURE=0`, and `flask --app holiday_planner run`). The SQLite database is
created under Flask's instance directory; set `DATABASE_PATH` to an absolute
path to choose its location. Back up the database (including any SQLite
journal/WAL files) using SQLite's online backup facilities. Restarting or
replacing ephemeral storage will otherwise lose data.

## Run with HTTPS (Caddy + Gunicorn)

A checked-in `Caddyfile` is provided at the repository root for automatic Let's
Encrypt TLS termination over Dynamic DNS or a custom domain. `caddy` must be
installed on the host separately, and ports `80` and `443` must reach it for
ACME certificate issuance and HTTPS traffic.

In GitHub's developer settings, register your OAuth app with homepage
`https://<domain>` and callback URL `https://<domain>/auth/callback`.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[deploy]'
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export GITHUB_CLIENT_ID='your-client-id'
export GITHUB_CLIENT_SECRET='your-client-secret'
export ALLOWED_GITHUB_IDS=353556 # comma-separated positive integer GitHub user IDs
export DATABASE_PATH='/var/lib/holiday-planner/holidays.sqlite3'
PLANNER_DOMAIN=your-domain.example ./scripts/run.sh
```

When `PLANNER_DOMAIN` is set, `./scripts/run.sh` automatically exports
`COOKIE_SECURE=1`, `TRUST_PROXY=1`,
`OAUTH_REDIRECT_URI="https://${PLANNER_DOMAIN}/auth/callback"`, and
`UPSTREAM_ADDR="${HOST}:${PORT}"` (default `127.0.0.1:5001`), starts Gunicorn
(`-w 1 --threads 4`), and runs `caddy run --config Caddyfile` in front of it
(stopping Gunicorn cleanly when Caddy exits).

`ALLOWED_GITHUB_IDS` defaults to `353556` (`@Lloydshove`) when unset and fails
closed at startup if set empty or invalid. `TRUST_PROXY=1` enables Werkzeug's
`ProxyFix` for one reverse-proxy hop (`X-Forwarded-For`, `X-Forwarded-Proto`,
`X-Forwarded-Host`, `X-Forwarded-Port`). Keep `COOKIE_SECURE=1`, a strong
stable `SECRET_KEY`, and regular SQLite backups enabled when exposing the app
on the internet.

Run checks with `pytest`.

## Working with Copilot

The repository includes focused Copilot custom-agent profiles and instructions
in `.github/`, inspired by [Microsoft HVE Core](https://github.com/microsoft/hve-core)'s
research, plan, implement and review approach. HVE Core is not installed or
vendored; its maintainers caution that it is rapidly evolving. Profiles are
instructions, not running services or proof of autonomous orchestration.

Create issues describing desired outcomes and acceptance criteria. Once a
supported issue-triggered orchestration has been configured, `ready-for-agent`
is the intended opt-in label. **Adding that label does not currently start an
agent.** For now, manually assign a suitable issue to Copilot if your GitHub
plan and repository settings support coding agent, and review its pull request.
No automatic merge or required branch checks are configured. See
[agent workflow](docs/agent-workflow.md) for the intended handoffs and blockers.
