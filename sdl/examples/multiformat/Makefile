PYTHON ?= python3
ROOT := ../..
BUILD_DIR ?= build
DEPS_DIR ?= $(BUILD_DIR)/deps
PYTHON_ENV := PYTHONPATH="$(abspath $(DEPS_DIR))$(if $(PYTHONPATH),:$(PYTHONPATH))"

.DEFAULT_GOAL := run

# Install only PyYAML, locally, when it is not already available.
deps:
	@$(PYTHON_ENV) $(PYTHON) -c "import yaml" >/dev/null 2>&1 || \
		$(PYTHON) -m pip --disable-pip-version-check install --target "$(DEPS_DIR)" -r $(ROOT)/sdlc/requirements-yaml.txt

import export: deps
	$(PYTHON_ENV) $(PYTHON) demo.py $@ --build-dir "$(BUILD_DIR)"

run: export import

test: run

clean:
	rm -rf "$(BUILD_DIR)"

.PHONY: deps export import run test clean
