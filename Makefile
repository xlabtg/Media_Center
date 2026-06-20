LOCAL_COMPOSE_FILE ?= infra/local/docker-compose.yml
LOCAL_ENV_FILE ?= infra/local/.env.local.example
LOCAL_PROJECT_NAME ?= media-center-local
BLOCKCHAIN_COMPOSE_FILE ?= infra/blockchain/docker-compose.yml
BLOCKCHAIN_PROFILE ?= blockchain

COMPOSE = docker compose --project-name $(LOCAL_PROJECT_NAME) --env-file $(LOCAL_ENV_FILE) -f $(LOCAL_COMPOSE_FILE)
BLOCKCHAIN_COMPOSE = $(COMPOSE) -f $(BLOCKCHAIN_COMPOSE_FILE)

.DEFAULT_GOAL := help

.PHONY: help up down migrate seed test ps logs clean backup-policy backup-local restore-drill blockchain-up blockchain-down blockchain-config blockchain-logs

help:
	@printf '%s\n' \
		'Media Center local development targets:' \
		'  make up       Start PostgreSQL, Redis, RabbitMQ, ChromaDB, MinIO and observability' \
		'  make migrate  Apply the local PostgreSQL schema and dev seed data' \
		'  make test     Validate the local environment contract' \
		'  make down     Stop the local stack' \
		'  make clean    Stop the local stack and remove volumes' \
		'  make backup-policy  Validate the Backup/DR policy JSON' \
		'  make backup-local   Print local Backup/DR backup commands' \
		'  make restore-drill  Print local Backup/DR restore drill' \
		'  make blockchain-config  Validate the private blockchain compose contract' \
		'  make blockchain-up      Start local stack with Besu/QBFT profile' \
		'  make blockchain-down    Stop local stack with Besu/QBFT profile'

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

backup-policy:
	python3 -m json.tool infra/backup/backup-policy.json >/dev/null

backup-local:
	bash infra/backup/scripts/backup.sh --dry-run all

restore-drill:
	bash infra/backup/scripts/restore_drill.sh --dry-run

blockchain-up:
	$(BLOCKCHAIN_COMPOSE) --profile $(BLOCKCHAIN_PROFILE) up -d

blockchain-down:
	$(BLOCKCHAIN_COMPOSE) --profile $(BLOCKCHAIN_PROFILE) down

blockchain-config:
	bash experiments/validate_issue79_blockchain_network.sh

blockchain-logs:
	$(BLOCKCHAIN_COMPOSE) --profile $(BLOCKCHAIN_PROFILE) logs -f --tail=200 besu-validator-1 besu-validator-2 besu-validator-3 besu-validator-4
