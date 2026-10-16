PYTHON ?= python3

ROOT := ../..
BUILD_DIR := build
GENERATED_DIR := $(BUILD_DIR)/generated

generate:
	mkdir -p $(GENERATED_DIR)
	$(PYTHON) $(ROOT)/gen/generator.py -python schema $(GENERATED_DIR)

run: generate
	PYTHONPATH=$(ROOT)/runtime/python:$(GENERATED_DIR)/python $(PYTHON) python/main.py

clean:
	rm -rf $(BUILD_DIR)

.PHONY: generate run clean
