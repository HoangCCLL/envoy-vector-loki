include envs/base.env
export

# Report defaults (override inline: make report PERIOD=4w TOP=20 UPSTREAM=binance-spot)
PERIOD   ?= 1w
TOP      ?= 10
OUTPUT   ?=
UPSTREAM ?=

# Edge node to operate on (override: make edge-up NODE=na)
NODE ?= us

EDGE_FLAGS = --env-file envs/base.env --env-file envs/$(NODE)-edge.env -p envoy-$(NODE) -f docker-compose.edge.yml

.PHONY: up down center-up center-down edge-up edge-down restart-edge render report help

help:
	@echo "Stack"
	@echo "  make up                        full stack (center + all nodes)"
	@echo "  make down                      stop full stack"
	@echo "  make center-up/down            Loki + Grafana only"
	@echo "  make edge-up   [NODE=us|na]    Envoy + Vector for one node (default: us)"
	@echo "  make edge-down [NODE=us|na]"
	@echo "  make restart-edge [NODE=us|na] re-render config + restart one node"
	@echo ""
	@echo "Upstreams"
	@echo "  edit upstreams.yaml, then make render (or make restart-edge)"
	@echo ""
	@echo "Report"
	@echo "  make report                           all upstreams, last 1w, top 10"
	@echo "  make report UPSTREAM=binance-spot     filter to 1 upstream"
	@echo "  make report PERIOD=4w TOP=20"
	@echo "  make report OUTPUT=report.md"
	@echo "  make report OUTPUT=report.csv"

## Stack ──────────────────────────────────────────────────────────────────────
up: center-up
	$(MAKE) edge-up NODE=us
	$(MAKE) edge-up NODE=na

down:
	$(MAKE) edge-down NODE=us
	$(MAKE) edge-down NODE=na
	$(MAKE) center-down

center-up:
	docker compose --env-file envs/base.env -f docker-compose.center.yml up -d --build

center-down:
	docker compose --env-file envs/base.env -f docker-compose.center.yml down

edge-up: render
	docker compose $(EDGE_FLAGS) up -d

edge-down:
	docker compose $(EDGE_FLAGS) down

restart-edge: render
	docker compose $(EDGE_FLAGS) restart

## Config ─────────────────────────────────────────────────────────────────────
render:
	python render.py

## Report ─────────────────────────────────────────────────────────────────────
report:
	cd report && python report.py \
		--period $(PERIOD) \
		--top    $(TOP) \
		--loki   http://localhost:$(LOKI_PORT) \
		$(if $(UPSTREAM),--upstream $(UPSTREAM),) \
		$(if $(OUTPUT),--output $(OUTPUT),)
