.PHONY: help build run run-ghcr lint

IMAGE      ?= chapkit-ewars-template:latest
GHCR_IMAGE ?= ghcr.io/chap-models/chapkit_ewars_template:latest

help:
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@echo "  build      Build docker image ($(IMAGE))"
	@echo "  run        Build and run the image on :8000"
	@echo "  run-ghcr   Pull and run the prebuilt GHCR image on :8000"
	@echo "  lint       Run ruff format check + lint"

build:
	@echo ">>> Building $(IMAGE)"
	@docker build --no-cache -t $(IMAGE) .

run: build
	@echo ">>> Running $(IMAGE) on :8000"
	@docker run --rm -p 8000:8000 --name chapkit-ewars-template $(IMAGE)

run-ghcr:
	@echo ">>> Running $(GHCR_IMAGE) on :8000"
	@docker run --rm --pull always --platform linux/amd64 -p 8000:8000 --name chapkit-ewars-template $(GHCR_IMAGE)

lint:
	@echo ">>> Ruff format check"
	@uv run ruff format --check .
	@echo ">>> Ruff lint"
	@uv run ruff check .

.DEFAULT_GOAL := help
