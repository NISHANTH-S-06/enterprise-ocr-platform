.PHONY: dev test migrate upgrade docker-up docker-down fmt

dev:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	pytest tests/ -v --tb=short

migrate:
	alembic revision --autogenerate -m "$(msg)"

upgrade:
	alembic upgrade head

downgrade:
	alembic downgrade -1

docker-up:
	docker-compose up -d

docker-down:
	docker-compose down

fmt:
	black app/ tests/
	isort app/ tests/
