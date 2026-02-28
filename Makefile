PYTHON ?= python3

.PHONY: up down seed test

seed:
	$(PYTHON) scripts/seed.py

up: seed
	docker compose up --build

down:
	docker compose down --remove-orphans

test:
	$(PYTHON) -m pytest
