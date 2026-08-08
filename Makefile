# 本地跑和 CI 完全一样的检查。推之前 `make check` 一次，别让 CI 当你的第一道防线。
.PHONY: check backend frontend fix

check: backend frontend

backend:
	uv run pytest -q
	uv run ruff check app/

frontend:
	cd frontend && npm run typecheck && npm run lint && npm run test && npm run build

# 能自动修的先修掉，再看剩下的
fix:
	uv run ruff check --fix app/
	cd frontend && npx eslint . --fix
