PYTHON ?= python3
OSE_ROOT := $(abspath ../..)
SDL_ROOT ?= $(abspath ../../../sdl)
export PYTHONPATH := $(OSE_ROOT)/src
export OSE_PARAMETER_TOKEN := local-example-parameters

.PHONY: build run list get set watch simulate
build:
	$(PYTHON) -m ose.cli build --model model.json --deployment deployment.json --sdl-root "$(SDL_ROOT)"
run: build
	$(PYTHON) -m ose.cli run
list:
	$(PYTHON) -m ose.cli parameters list --client operator
get:
	$(PYTHON) -m ose.cli parameters get machines/press_1/detection --client operator
set:
	$(PYTHON) -m ose.cli parameters set machines/press_1/detection --client operator --file detection.json
watch:
	$(PYTHON) -m ose.cli parameters watch machines/press_1/detection --client operator
simulate: build
	$(PYTHON) -m ose.cli run --mode simulated --duration 2 --workers 2 --scenario scenario.json --trace build/trace.jsonl
