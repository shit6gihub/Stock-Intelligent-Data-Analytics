# Repository Guidelines

## Project Structure & Module Organization
- `src/agents/` — Agent implementations (business logic). Add new agents here.
- `src/collectors/` — Data collectors (quotes, kline, news, etc.).
- `src/core/` — Core utilities (AI client, notifier, scheduler helpers).
- `src/web/` — FastAPI app (models, API routes, DB setup).
- `frontend/` — React + TypeScript (Vite + Tailwind). UI lives in `frontend/src/`.
- `prompts/` — Prompt templates used by agents.
- `config/`, `data/` — Config files and runtime data (persisted at `DATA_DIR`).
- `server.py` — Backend entrypoint; also registers agents and data sources.
- `tests/` — Placeholder for backend tests.
- `build.sh`, `Dockerfile` — Build frontend and container images.

## Build, Test, and Development Commands
- Backend (dev): `make dev-api`（自动 venv+依赖+uvicorn reload，监听 `:8000`）；或手动 `python server.py`。
- Frontend (dev): `make dev-web`（自动 pnpm install+dev，served on `http://localhost:5183`）。
- Frontend (build): `cd frontend && pnpm install --frozen-lockfile && pnpm build`.
- Docker image: `./build.sh <version>` (copies `frontend/dist` to `./static` and builds image).
- Run via Docker: `docker run -d -p 8000:8000 -v panwatch_data:/app/data xiaoze-hub/stock-intelligent-data-analytics:latest`.
- Tests (backend): add pytest tests under `tests/` then run `pytest`.
- Development lifecycle: routine source changes should use hot reload or restart the affected service. Rebuild Docker images only for release builds or when changing content that is not mounted into the development container, such as packaged frontend assets, dependencies, Dockerfiles, or installed local packages.
- Docker cleanup: after every image build, first confirm the replacement containers are healthy, then remove obsolete PanWatch images and temporary validation images that are not referenced by any container. Never remove running images or data volumes, and do not broadly prune shared build caches without explicit approval.

## Coding Style & Naming Conventions
- Python: PEP 8, 4-space indent, type hints required for new code. Files `snake_case.py`, classes `PascalCase`, functions/vars `snake_case`.
- Agents: implement in `src/agents/*.py`, register in `server.py` (`AGENT_REGISTRY`) and seed config in `seed_agents()`.
- Collectors: place in `src/collectors/`, keep stateless; return typed dataclasses.
- TypeScript: components `PascalCase.tsx` in `frontend/src/`, hooks `use-` prefix, utilities `camelCase.ts`.
- Prompts: one prompt file per agent in `prompts/` (e.g., `daily_report.txt`).

## Testing Guidelines
- Backend: structure tests as `tests/test_<module>.py`; prefer fast, isolated unit tests around agents, collectors, and core.
- Coverage: target meaningful coverage for new modules (no strict threshold yet, but include happy-path and error cases).
- Fixtures: use factory helpers for DB models; avoid network calls (mock collectors and AI clients).

## Commit & Pull Request Guidelines
- Commit format: `<type>: <subject>` where type ∈ `{fix, feature, update, doc}`.
- Keep the type prefix in English, and write the subject after the colon (plus any optional commit body) in Chinese.
  Example: `feature: 新增盘中监控 Agent`.
- Keep one logical, reviewable change per commit. Once a change is ready to record, commit it instead of accumulating unrelated work.
- Every commit must update `CHANGELOG.md` in the same commit. Add a concise entry under the current date and one of these headings:
  - `fix` — bug fixes and regression corrections.
  - `feature` — new user-facing or developer-facing capabilities.
  - `update` — changes to existing behavior, dependencies, configuration, refactors, tests, or operations.
  - `doc` — documentation and development-process changes.
- Do not create a code-only commit followed by a separate changelog commit; the change and its changelog entry are one atomic commit.
- Pull Requests: include a clear description, linked issues, and screenshots/GIFs for UI changes. Update docs/prompts when applicable.
- CI hygiene: ensure backend runs (`python server.py`) and frontend builds (`pnpm build`). No secrets in commits; use `.env` or UI settings.

## Security & Configuration Tips
- Secrets: do not commit API keys; configure via UI or env vars (`.env`, `AUTH_USERNAME`, `AUTH_PASSWORD`, `JWT_SECRET`, `DATA_DIR`).
- Network/SSL: optional corporate CA via `data/ca-bundle.pem` is auto-managed; respect `HTTP(S)_PROXY`/app proxy settings.
- Playwright: in Docker, browsers install under `DATA_DIR/playwright` automatically; local dev uses system install.
