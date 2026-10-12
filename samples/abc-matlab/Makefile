PYTHON ?= python3
OCTAVE ?= octave
matlab ?= matlab

ROOT := ../..
BUILD_DIR := build
GENERATED_DIR := $(BUILD_DIR)/generated
matlab_available := $(shell command -v $(matlab) >/dev/null 2>&1 && echo yes)
ifeq ($(matlab_available),yes)
MATLAB_RUN = $(matlab) -batch "addpath('$(GENERATED_DIR)/matlab'); addpath('matlab'); main($(ARGS))"
else
MATLAB_RUN = $(OCTAVE) --quiet --no-gui --eval "addpath('$(GENERATED_DIR)/matlab'); addpath('matlab'); main($(ARGS))"
endif

generate:
	mkdir -p $(GENERATED_DIR)
	$(PYTHON) $(ROOT)/gen/generator.py -matlab schema $(GENERATED_DIR)

run: generate
	$(MATLAB_RUN)

clean:
	rm -rf $(BUILD_DIR)

.PHONY: generate run clean
