# Holiday planner

A small, responsive web application for keeping track of trips. Users sign in
with GitHub and see only their own trips. Trip data is stored in SQLite on the
server. This is a starter, not a production-ready public deployment.

## Run locally

Requires Python 3.12 or later and a GitHub OAuth app. In GitHub's developer
settings, create an OAuth app with homepage `http://127.0.0.1:5000` and
callback URL `http://127.0.0.1:5000/auth/callback`. Do not commit credentials.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export GITHUB_CLIENT_ID='your-client-id'
export GITHUB_CLIENT_SECRET='your-client-secret'
export OAUTH_REDIRECT_URI='http://127.0.0.1:5000/auth/callback'
export COOKIE_SECURE=0 # local HTTP only
flask --app holiday_planner run
```

Open <http://127.0.0.1:5000>. The SQLite database is created under Flask's
instance directory; set `DATABASE_PATH` to an absolute path to choose its
location. Back up the database (including any SQLite journal/WAL files) using
SQLite's online backup facilities. Restarting or replacing ephemeral storage
will otherwise lose data.

Before deploying publicly, configure HTTPS, a persistent volume, a strong
stable `SECRET_KEY`, GitHub OAuth callback URL, backups and a production WSGI
server. `COOKIE_SECURE` defaults to enabled and must remain enabled for HTTPS.
Set `OAUTH_REDIRECT_URI` to the exact HTTPS callback registered with GitHub.
Review authentication, operational security, privacy, rate limiting, database
migrations and deployment requirements before exposing this starter publicly.
No hosting or deployment has been configured.

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
