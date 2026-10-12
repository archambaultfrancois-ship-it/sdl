PYTHON ?= python3
CC ?= gcc
CARGO ?= cargo
CFLAGS ?= -std=c99 -Wall -Wextra -pedantic -O2
BUILD_DIR := build
GENERATED_DIR := $(BUILD_DIR)/generated
RUST_GENERATED_DIR := $(GENERATED_DIR)/rust
SDL_FILES := $(wildcard sdl/*.sdl)

all: $(BUILD_DIR)/sdl_demo

test: test-c test-rust

test-c: $(BUILD_DIR)/sdl_demo
	./$(BUILD_DIR)/sdl_demo

test-rust: $(RUST_GENERATED_DIR)/.stamp
	$(CARGO) test --manifest-path tst/rust/Cargo.toml

$(GENERATED_DIR)/.stamp: $(SDL_FILES) gen/generator.py Makefile
	mkdir -p $(GENERATED_DIR)
	$(PYTHON) gen/generator.py sdl $(GENERATED_DIR)
	touch $@

$(RUST_GENERATED_DIR)/.stamp: $(SDL_FILES) gen/generator.py gen/rust_backend.py Makefile
	mkdir -p $(RUST_GENERATED_DIR)
	$(PYTHON) gen/rust_backend.py sdl $(RUST_GENERATED_DIR)
	touch $@

$(GENERATED_DIR)/schema.h $(GENERATED_DIR)/codec_cases.h $(GENERATED_DIR)/sdl_registry.h: $(GENERATED_DIR)/.stamp

$(BUILD_DIR)/sdl_demo: runtime/c/type_engine.c runtime/c/type_registry.c runtime/c/type_codec.c runtime/c/sdl_wire.c tst/main.c $(GENERATED_DIR)/.stamp $(GENERATED_DIR)/schema.h $(GENERATED_DIR)/codec_cases.h $(GENERATED_DIR)/sdl_registry.h
	mkdir -p $(BUILD_DIR)
	$(CC) $(CFLAGS) -Iruntime/c -I$(GENERATED_DIR) runtime/c/type_engine.c runtime/c/type_registry.c runtime/c/type_codec.c runtime/c/sdl_wire.c $(GENERATED_DIR)/*.c tst/main.c -lm -o $@

clean:
	rm -rf $(BUILD_DIR)

.PHONY: all clean test test-c test-rust
