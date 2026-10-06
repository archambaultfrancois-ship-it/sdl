PYTHON ?= python3
WIRE_ENDIAN ?= big

ROOT := ../..
BUILD_DIR := build
GENERATED_DIR := $(BUILD_DIR)/generated
ifeq ($(WIRE_ENDIAN),little)
PYTHON_ENDIAN := SDL_WIRE_ENDIAN=little
else ifneq ($(WIRE_ENDIAN),big)
$(error WIRE_ENDIAN must be big or little)
endif

generate:
	mkdir -p $(GENERATED_DIR)
	$(PYTHON) $(ROOT)/gen/generator.py -python schema $(GENERATED_DIR)

run: generate
	$(PYTHON_ENDIAN) PYTHONPATH=$(ROOT)/runtime/python:$(GENERATED_DIR)/python $(PYTHON) python/main.py

clean:
	rm -rf $(BUILD_DIR)

.PHONY: generate run clean
