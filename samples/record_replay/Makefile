PYTHON ?= python3
ROOT := ../..
RECORDING_DEPS ?= $(ROOT)/build/recording-deps
PYTHON_ENV := PYTHONPATH=$(RECORDING_DEPS):$(ROOT)/runtime/python:$(CURDIR)/build/generated/python:$(CURDIR)
CLI := $(ROOT)/tools/recording/sdl-recording

generate:
	$(PYTHON) $(ROOT)/gen/generator.py -python ../radio_capture/schema build/generated

record: generate
	$(PYTHON_ENV) $(PYTHON) main.py build/radio.mcap --overwrite > build/live.log

demo: record
	$(PYTHON_ENV) $(PYTHON) $(CLI) info build/radio.mcap
	$(PYTHON_ENV) $(PYTHON) $(CLI) inspect build/radio.mcap --format text --from 300ms --to 400ms --limit 1
	$(PYTHON_ENV) $(PYTHON) $(CLI) validate build/radio.mcap
	$(PYTHON_ENV) $(PYTHON) $(CLI) replay build/radio.mcap --speed 0 --consumer pipeline:on_record > build/replayed.log
	cmp build/live.log build/replayed.log
	@echo "Live and replay analysis produced identical results."

.PHONY: generate record demo
