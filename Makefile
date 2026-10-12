PYTHON ?= python3
CC ?= gcc
CARGO ?= cargo
CFLAGS ?= -std=c99 -Wall -Wextra -pedantic -O2
BUILD_DIR := build
GENERATED_DIR := $(BUILD_DIR)/generated
C_GENERATED_DIR := $(GENERATED_DIR)/c
RUST_GENERATED_DIR := $(GENERATED_DIR)/rust
PYTHON_GENERATED_DIR := $(GENERATED_DIR)/python
SDL_FILES := $(wildcard sdl/*.sdl)

all: $(BUILD_DIR)/sdl_demo

test: test-c test-rust test-python

test-c: $(BUILD_DIR)/sdl_demo
	./$(BUILD_DIR)/sdl_demo

test-rust: $(GENERATED_DIR)/.stamp
	$(CARGO) test --manifest-path tst/rust/Cargo.toml

test-python: $(GENERATED_DIR)/.stamp
	PYTHONPATH=runtime/python:$(PYTHON_GENERATED_DIR) $(PYTHON) -m unittest discover -s tst/python -v

$(GENERATED_DIR)/.stamp: $(SDL_FILES) gen/generator.py gen/c_backend.py gen/rust_backend.py gen/python_backend.py Makefile
	mkdir -p $(GENERATED_DIR)
	$(PYTHON) gen/generator.py -c -rust -python sdl $(GENERATED_DIR)
	touch $@

$(C_GENERATED_DIR)/schema.h $(C_GENERATED_DIR)/codec_cases.h $(C_GENERATED_DIR)/sdl_registry.h: $(GENERATED_DIR)/.stamp

$(BUILD_DIR)/sdl_demo: runtime/c/type_engine.c runtime/c/type_registry.c runtime/c/type_codec.c runtime/c/sdl_wire.c tst/main.c $(GENERATED_DIR)/.stamp $(C_GENERATED_DIR)/schema.h $(C_GENERATED_DIR)/codec_cases.h $(C_GENERATED_DIR)/sdl_registry.h
	mkdir -p $(BUILD_DIR)
	$(CC) $(CFLAGS) -Iruntime/c -I$(C_GENERATED_DIR) runtime/c/type_engine.c runtime/c/type_registry.c runtime/c/type_codec.c runtime/c/sdl_wire.c $(C_GENERATED_DIR)/*.c tst/main.c -lm -o $@

clean:
	rm -rf $(BUILD_DIR)

.PHONY: all clean test test-c test-rust test-python
