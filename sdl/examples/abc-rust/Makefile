PYTHON ?= python3
CARGO ?= cargo

ROOT := ../..
BUILD_DIR := build
GENERATED_DIR := $(BUILD_DIR)/generated

generate:
	mkdir -p $(GENERATED_DIR)
	$(PYTHON) $(ROOT)/gen/generator.py -rust schema $(BUILD_DIR)/generated

run: generate
	$(CARGO) run --manifest-path Cargo.toml

clean:
	rm -rf $(BUILD_DIR) target

.PHONY: generate run clean
