# Open Gong — one-command demo. Requires: uv (https://docs.astral.sh/uv), Node 20+.

.PHONY: demo backend web install test clean

demo: install ## Boot backend + web with the five sample calls (zero API keys needed)
	@echo "Starting Open Gong demo — http://localhost:3000"
	@trap 'kill 0' EXIT; \
	(cd backend && uv run uvicorn app.main:app --port 8000) & \
	(cd web && npm run dev) & \
	wait

install:
	cd backend && uv sync
	cd web && npm install

init: ## First-run setup: configure LLM + PyAI keys (interactive)
	cd backend && uv run python -m app.cli init

doctor: ## Show what's configured
	cd backend && uv run python -m app.cli doctor

backend:
	cd backend && uv run uvicorn app.main:app --port 8000 --reload

web:
	cd web && npm run dev

test:
	cd backend && uv run pytest

clean:
	rm -rf backend/.venv web/node_modules web/.next
