.PHONY: up down run health test

test:
	pytest

up:
	docker compose up --build

down:
	docker compose down

# 在仓库根目录起：.env 和 ./data 都按当前目录找。原来 cd 进 server/ 再起，
# 两样都找不到——key 空、记忆 0、向量关（09-16 实测）。
run:
	uvicorn app.main:app --app-dir server --reload --port 8787

health:
	curl -s http://localhost:8787/health
