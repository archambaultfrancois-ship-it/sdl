PYTHON ?= python3
CARGO ?= cargo
WIRE_ENDIAN ?= big

ROOT := ../..
BUILD_DIR := build
GENERATED_DIR := $(BUILD_DIR)/generated
RUST_FEATURES :=
ifeq ($(WIRE_ENDIAN),little)
RUST_FEATURES := --features wire-little-endian
else ifneq ($(WIRE_ENDIAN),big)
$(error WIRE_ENDIAN must be big or little)
endif

generate:
	mkdir -p $(GENERATED_DIR)
	$(PYTHON) $(ROOT)/gen/generator.py -rust schema $(BUILD_DIR)/generated

run: generate
	$(CARGO) run --manifest-path Cargo.toml $(RUST_FEATURES)

clean:
	rm -rf $(BUILD_DIR) target

.PHONY: generate run clean
