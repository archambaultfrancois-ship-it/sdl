PYTHON ?= python3
OCTAVE ?= octave
matlab ?= matlab
WIRE_ENDIAN ?= big

ROOT := ../..
BUILD_DIR := build
GENERATED_DIR := $(BUILD_DIR)/generated
matlab_available := $(shell command -v $(matlab) >/dev/null 2>&1 && echo yes)
ifeq ($(matlab_available),yes)
MATLAB_RUN = SDL_WIRE_ENDIAN=$(WIRE_ENDIAN) $(matlab) -batch "addpath('$(GENERATED_DIR)/matlab'); addpath('matlab'); main($(ARGS))"
else
MATLAB_RUN = SDL_WIRE_ENDIAN=$(WIRE_ENDIAN) $(OCTAVE) --quiet --no-gui --eval "addpath('$(GENERATED_DIR)/matlab'); addpath('matlab'); main($(ARGS))"
endif
ifeq ($(filter $(WIRE_ENDIAN),big little),)
$(error WIRE_ENDIAN must be big or little)
endif

generate:
	mkdir -p $(GENERATED_DIR)
	$(PYTHON) $(ROOT)/gen/generator.py -matlab schema $(GENERATED_DIR)

run: generate
	$(MATLAB_RUN)

clean:
	rm -rf $(BUILD_DIR)

.PHONY: generate run clean
