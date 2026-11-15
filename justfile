# elbot's checks, the same ones CI runs (.github/workflows/ci.yml).

default: check

# Everything CI runs: lint, formatting, types and tests
check: lint types test

# ruff's lint rules and its formatting
lint:
    uv run ruff check .
    uv run ruff format --check .

# ty, with warnings counted as failures
types:
    uv run ty check . --error-on-warning

# The test suite (offline, no Telegram)
test:
    uv run pytest

# Mend what ruff can: fixable lint rules, then formatting
fix:
    uv run ruff check --fix .
    uv run ruff format .

# Turn on the pre-commit hook in .githooks (once per clone)
hooks:
    git config core.hooksPath .githooks
