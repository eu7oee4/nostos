.PHONY: up down run health

up:
	docker compose up --build

down:
	docker compose down

run:
	cd server && PYTHONPATH=. uvicorn app.main:app --reload --port 8787

health:
	curl -s http://localhost:8787/health
