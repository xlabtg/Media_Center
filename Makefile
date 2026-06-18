LOCAL_COMPOSE_FILE ?= infra/local/docker-compose.yml
LOCAL_ENV_FILE ?= infra/local/.env.local.example
LOCAL_PROJECT_NAME ?= media-center-local

COMPOSE = docker compose --project-name $(LOCAL_PROJECT_NAME) --env-file $(LOCAL_ENV_FILE) -f $(LOCAL_COMPOSE_FILE)

.DEFAULT_GOAL := help

.PHONY: help up down migrate seed test ps logs clean

help:
	@printf '%s\n' \
		'Media Center local development targets:' \
		'  make up       Start PostgreSQL, Redis, RabbitMQ, ChromaDB and MinIO' \
		'  make migrate  Apply the local PostgreSQL schema and dev seed data' \
		'  make test     Validate the local environment contract' \
		'  make down     Stop the local stack' \
		'  make clean    Stop the local stack and remove volumes'

up:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

migrate:
	LOCAL_COMPOSE_FILE=$(LOCAL_COMPOSE_FILE) \
	LOCAL_ENV_FILE=$(LOCAL_ENV_FILE) \
	LOCAL_PROJECT_NAME=$(LOCAL_PROJECT_NAME) \
	bash infra/local/scripts/migrate.sh

seed:
	LOCAL_COMPOSE_FILE=$(LOCAL_COMPOSE_FILE) \
	LOCAL_ENV_FILE=$(LOCAL_ENV_FILE) \
	LOCAL_PROJECT_NAME=$(LOCAL_PROJECT_NAME) \
	bash infra/local/scripts/seed.sh

test:
	bash experiments/validate_issue10_local_env.sh

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail=200

clean:
	$(COMPOSE) down -v --remove-orphans
