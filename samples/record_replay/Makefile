PYTHON ?= python3
ROOT := ../..
RECORDING_DEPS ?= $(ROOT)/build/recording-deps
PYTHON_ENV := PYTHONPATH=$(RECORDING_DEPS):$(ROOT)/runtime/python:$(CURDIR)/build/generated/python:$(CURDIR)
CLI := $(ROOT)/tools/recording/sdl-recording
RECORDING_FORMAT ?= mcap
CAPTURE := build/radio.$(RECORDING_FORMAT)

generate:
	$(PYTHON) $(ROOT)/gen/generator.py -python ../radio_capture/schema build/generated

recording-deps:
	@PYTHONPATH="$(RECORDING_DEPS)" $(PYTHON) -c "from importlib.metadata import version; from mcap.writer import Writer; from mcap.stream_reader import StreamReader; assert version('mcap') == '1.5.0'" >/dev/null 2>&1 || \
		$(MAKE) --no-print-directory -C $(ROOT) recording-deps PYTHON="$(PYTHON)" RECORDING_DEPS="$(abspath $(RECORDING_DEPS))"

record: generate recording-deps
	$(PYTHON_ENV) $(PYTHON) main.py $(CAPTURE) --format $(RECORDING_FORMAT) --overwrite > build/live.log

demo: record
	$(PYTHON_ENV) $(PYTHON) $(CLI) info $(CAPTURE)
	$(PYTHON_ENV) $(PYTHON) $(CLI) inspect $(CAPTURE) --format text --from 300ms --to 400ms --limit 1
	$(PYTHON_ENV) $(PYTHON) $(CLI) validate $(CAPTURE)
	$(PYTHON_ENV) $(PYTHON) $(CLI) replay $(CAPTURE) --speed 0 --consumer pipeline:on_record > build/replayed.log
	cmp build/live.log build/replayed.log
	@echo "Live and replay analysis produced identical results."

.PHONY: generate recording-deps record demo
