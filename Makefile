# Configuration
ENV ?= dev

# Détermination des fichiers compose à utiliser
ifeq ($(ENV), prod)
	COMPOSE_FILES := -f docker-compose.yml
	MSG := "--- Mode PRODUCTION ---"
else
	COMPOSE_FILES := -f docker-compose.yml -f docker-compose.override.yml
	MSG := "--- Mode DÉVELOPPEMENT (Hot-reload activé) ---"
endif

COMPOSE := docker compose $(COMPOSE_FILES)

.PHONY: help up down build restart logs ps shell-web shell-worker shell-db

help:
	@echo "Usage: make [target] [ENV=prod|dev]"
	@echo ""
	@echo "Targets:"
	@echo "  up             Démarrer les conteneurs en arrière-plan"
	@echo "  down           Arrêter et supprimer les conteneurs"
	@echo "  build          Construire ou reconstruire les images"
	@echo "  restart        Redémarrer les services"
	@echo "  logs           Afficher les logs en temps réel"
	@echo "  ps             Lister les conteneurs en cours"
	@echo "  shell-web      Ouvrir un shell dans le conteneur Web"
	@echo "  shell-worker   Ouvrir un shell dans le conteneur Worker"
	@echo "  shell-db       Ouvrir un shell psql dans la base de données"

up:
	@echo $(MSG)
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

build:
	$(COMPOSE) build

restart:
	$(COMPOSE) restart

logs:
	$(COMPOSE) logs -f

ps:
	$(COMPOSE) ps

shell-web:
	$(COMPOSE) exec web bash

shell-worker:
	$(COMPOSE) exec worker bash

shell-db:
	$(COMPOSE) exec db psql -U postgres -d fdr_db
