PYTHON ?= python3
ROOT := ../..
RECORDING_DEPS ?= $(ROOT)/build/recording-deps
PYTHON_ENV := PYTHONPATH=$(RECORDING_DEPS):$(ROOT)/runtime/python:$(ROOT)/runtime/filter/python:$(ROOT)/build/filter/release:$(CURDIR)/build/generated/python:$(CURDIR)
CLI := $(ROOT)/tools/recording/sdl-recording
RECORDING_FORMAT ?= pcapng
CAPTURE := build/radio.$(RECORDING_FORMAT)

generate:
	$(PYTHON) $(ROOT)/gen/generator.py -python ../radio_capture/schema build/generated

recording-deps:
	@PYTHONPATH="$(RECORDING_DEPS)" $(PYTHON) -c "from importlib.metadata import version; from mcap.writer import Writer; from mcap.stream_reader import StreamReader; assert version('mcap') == '1.5.0'" >/dev/null 2>&1 || \
		$(MAKE) --no-print-directory -C $(ROOT) recording-deps PYTHON="$(PYTHON)" RECORDING_DEPS="$(abspath $(RECORDING_DEPS))"

filter-python:
	$(MAKE) --no-print-directory -C $(ROOT) filter-python PYTHON="$(PYTHON)"

record: generate recording-deps filter-python
	$(PYTHON_ENV) $(PYTHON) main.py $(CAPTURE) --format $(RECORDING_FORMAT) --overwrite --filter-log build/live-filtered.log > build/live.log

demo: record
	$(PYTHON_ENV) $(PYTHON) $(CLI) info $(CAPTURE)
	$(PYTHON_ENV) $(PYTHON) $(CLI) inspect $(CAPTURE) --format text --from 300ms --to 400ms --limit 1
	$(PYTHON_ENV) $(PYTHON) $(CLI) validate $(CAPTURE)
	$(PYTHON_ENV) $(PYTHON) $(CLI) replay $(CAPTURE) --speed 0 --consumer pipeline:on_record > build/replayed.log
	cmp build/live.log build/replayed.log
	@echo "Live and replay analysis produced identical results."
	$(PYTHON_ENV) $(PYTHON) verify_filters.py $(CAPTURE) build/replayed-filtered.log
	cmp build/live-filtered.log build/replayed-filtered.log
	@echo "Live object filtering and recorded payload filtering produced identical results."

.PHONY: generate recording-deps filter-python record demo
