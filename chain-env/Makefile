PYTHON ?= python3
SDL_ROOT ?= $(abspath $(CURDIR)/..)
export PYTHONPATH := $(CURDIR)/src
MODEL := examples/acquisition/model.json
DEPLOYMENT := examples/acquisition/deployment.json

.PHONY: validate build demo test test-unit test-integration test-sdk
validate:
	$(PYTHON) -m chain_env.cli validate --model $(MODEL) --deployment $(DEPLOYMENT) --sdl-root "$(SDL_ROOT)"
build:
	$(PYTHON) -m chain_env.cli build --model $(MODEL) --deployment $(DEPLOYMENT) --sdl-root "$(SDL_ROOT)"
demo: build
	$(PYTHON) -m chain_env.cli run --duration 2
	$(PYTHON) -m chain_env.cli inspect --last
test: test-unit test-sdk test-integration
test-unit:
	$(PYTHON) -m unittest discover -s tests/unit -v
test-sdk:
	CARGO_TARGET_DIR=$(CURDIR)/build/sdk-rust cargo test --offline --manifest-path sdk/rust/Cargo.toml
test-integration:
	SDL_ROOT="$(SDL_ROOT)" $(PYTHON) -m unittest discover -s tests/integration -v
