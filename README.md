# Dify Cloud Status (Unofficial)

An unofficial, independently operated status page for [Dify Cloud](https://cloud.dify.ai). Monitors service health via periodic checks, stores results as JSONL, builds a static site with a 90-day status grid, and deploys to GitHub Pages.

**Live site:** https://kurokobo.github.io/dify-status/

> **Disclaimer:** This project is not affiliated with, endorsed by, or supported by Dify or LangGenius in any way. This is a personal project in an early/alpha stage. Behavior, check configurations, and results may change at any time without prior notice. No guarantees are made regarding the accuracy or reliability of results. The project may be discontinued at any time.

## Monitored Checks

| ID | Name | What it does |
|---|---|---|
| `web_ui` | Web UI | GET `cloud.dify.ai`, expect HTTP 200 |
| `api` | API | POST chat-messages (Start + Answer flow), expect body contains `pong` |
| `sandbox` | Sandbox | POST chat-messages (Start + Template + Answer flow), expect body contains `pong from sandbox`. Depends on API. |
| `plugin` | Plugin | POST chat-messages (Start + LLM with Fake Models + Answer flow), expect body contains `pong from plugin`. Depends on API. |
| `indexing_free` | Knowledge Indexing (Free Plan) | Upload a small document on the Free (Sandbox) plan, verify indexing queue processes it. |
| `indexing_pro` | Knowledge Indexing (Pro Plan) | Upload a small document on the Pro plan, verify indexing queue processes it. May stop working due to the subscription period ending. |
| `retrieve` | Knowledge Retrieval | POST a semantic search query to a pre-built High-Quality knowledge base, verify the vector DB responds with a `records` field. |
| `webhook_free` | Webhook Trigger (Free Plan) | Trigger a workflow via webhook on the Free plan, verify it is processed. |
| `webhook_pro` | Webhook Trigger (Pro Plan) | Trigger a workflow via webhook on the Pro plan, verify it is processed. May stop working due to the subscription period ending. |

## Notifications

Status change notifications are posted as comments to [GitHub Issue #1](https://github.com/kurokobo/dify-status/issues/1).

- **Incident detected**: posted when any check transitions from healthy to unhealthy
- **Recovered**: posted when all checks return to healthy

Subscribe to (watch) the issue to receive email notifications from GitHub.

## Running Manually

This project uses [uv](https://docs.astral.sh/uv/) for Python dependency management.

```bash
# Run all checks and append results to data/
uv run python -m checks.runner

# Post GitHub Issue comments on status transitions (requires GH_TOKEN)
uv run python -m checks.notify

# Build the static site into site/
uv run python -m build.build

# Remove data older than retention_days
uv run python -m build.cleanup

# Preview the built site locally
uv run python -m http.server -d site 8000
```

## Architecture

- **Checks** (`checks/`) run every 15 minutes, triggered by [cron-job.org](https://cron-job.org) via GitHub Actions `workflow_dispatch`, appending results to `data/YYYY/MM/YYYY-MM-DD.jsonl`
- **Notifications** (`checks/notify.py`) detect status transitions and post comments to a GitHub Issue via the `gh` CLI
- **Build** (`build/build.py`) reads all JSONL data, computes 90-day summaries, and renders Jinja2 templates into `site/`
- **Deploy** (`build-pages.yml`) triggers on data/config changes and publishes `site/` to GitHub Pages
- **Data** is retained for 90 days (configurable in `config.yaml`)

### Check Types

| Type | Description |
|---|---|
| `http` | Single-cycle: sends an HTTP request and checks status code and/or body |
| `knowledge` | Two-cycle: uploads a document in cycle N, checks indexing status in cycle N+1 |
| `retrieve` | Single-cycle: POSTs a semantic search query and verifies the response |
| `webhook` | Two-cycle: triggers a webhook in cycle N, checks workflow execution in cycle N+1 |

## Tech Stack

- **Python 3.12** with **uv**
- **httpx** for async HTTP checks
- **Jinja2** for HTML templating
- **PyYAML** for config
- **Alpine.js** + **@alpinejs/collapse** for frontend interactivity
- **GitHub Actions** for automation
- **GitHub Pages** for hosting
