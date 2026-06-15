# TRIDENT developer convenience targets.
# Docker is the supported path (`make up`); these run services locally for dev.

PYTHONPATH := packages/contracts:packages/common:packages/geo
export PYTHONPATH

.PHONY: help up down logs install ingestor counts cognition api replay web test fmt

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

up:          ## docker compose up --build (the supported full-stack boot)
	docker compose up --build

down:        ## stop the stack
	docker compose down

logs:        ## tail all service logs
	docker compose logs -f

install:     ## editable-install the spine + service deps for local dev
	./scripts/dev_install.sh

ingestor:    ## run the ingestor (synthetic feed by default)
	cd services/ingestor && PYTHONPATH=$(PYTHONPATH):. python -m ingestor.main

counts:      ## M1 proof: live per-zone vessel counts
	cd services/ingestor && PYTHONPATH=$(PYTHONPATH):. python -m ingestor.cli counts

cognition:   ## run the LangGraph cognition swarm
	cd services/cognition && PYTHONPATH=$(PYTHONPATH):. python -m cognition.main

api:         ## run the FastAPI gateway on :8000
	cd services/api && PYTHONPATH=$(PYTHONPATH):. uvicorn api.main:app --reload --port 8000

replay:      ## run the replay service on :8100
	cd services/replay && PYTHONPATH=$(PYTHONPATH):. uvicorn replay.main:app --reload --port 8100

web:         ## run the Next.js command center on :3000
	cd web && npm run dev

test:        ## run the python test suites
	PYTHONPATH=$(PYTHONPATH):services/ingestor python -m pytest services/ingestor/tests -q
	PYTHONPATH=$(PYTHONPATH):services/cognition python -m pytest services/cognition/tests -q
