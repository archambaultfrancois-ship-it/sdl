PYTHON ?= python3
ROOT := ../..
export PYTHONPATH := $(abspath $(ROOT)/runtime/python):$(abspath build/v1/python):$(abspath build/v2/python):$(abspath build/bad/python)

.PHONY: generate demo test broker validate

generate:
	$(PYTHON) $(ROOT)/sdlc/generator.py -python schema/temperature_v1.sdl build/v1
	$(PYTHON) $(ROOT)/sdlc/generator.py -python schema/temperature_v2.sdl build/v2
	$(PYTHON) $(ROOT)/sdlc/generator.py -python schema/temperature_bad.sdl build/bad

demo: generate
	$(PYTHON) app.py demo

test: generate
	$(PYTHON) -m unittest -v test_contracts.py test_resolution.py test_poc.py

broker: generate
	$(PYTHON) app.py broker

CONTRACT ?= contracts/temperature.json
CATALOGUE ?= contracts/temperature_v1.sdl2

validate:
	$(PYTHON) contracts.py validate --contract "$(CONTRACT)" --catalogue "$(CATALOGUE)"
