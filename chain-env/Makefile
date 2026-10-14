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
	CARGO_TARGET_DIR=$(CURDIR)/build/client-rust cargo test --offline --manifest-path sdk/client/rust/Cargo.toml
test-integration:
	SDL_ROOT="$(SDL_ROOT)" $(PYTHON) -m unittest discover -s tests/integration -v

.PHONY: client-native external-types external-native
client-native:
	CARGO_TARGET_DIR=$(CURDIR)/build/client-rust cargo build --offline --manifest-path sdk/client/rust/Cargo.toml
external-types:
	$(PYTHON) "$(SDL_ROOT)/gen/generator.py" -python -c -rust examples/acquisition/schemas build/generated/shared
external-native: external-types client-native
	CARGO_TARGET_DIR=$(CURDIR)/build/client-example cargo build --offline --manifest-path examples/external/rust/Cargo.toml
	$(CC) -std=c11 -D_POSIX_C_SOURCE=200809L -Wall -Wextra -Werror -O2 -Isdk/client/c -Ibuild/generated/shared/c -I"$(SDL_ROOT)/runtime/c" sdk/client/c/chain_client.c examples/external/client.c build/generated/shared/c/*.c "$(SDL_ROOT)/runtime/c/type_engine.c" "$(SDL_ROOT)/runtime/c/type_registry.c" "$(SDL_ROOT)/runtime/c/type_codec.c" "$(SDL_ROOT)/runtime/c/sdl_wire.c" "$(SDL_ROOT)/runtime/c/sdl_dynamic.c" "$(SDL_ROOT)/runtime/c/type_display.c" build/client-rust/debug/libsdl_chain_client.a -ldl -lm -lpthread -o build/external-c
