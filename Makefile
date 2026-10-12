PYTHON ?= python3
CC ?= gcc
CARGO ?= cargo
CFLAGS ?= -std=c99 -Wall -Wextra -pedantic -O2
BENCH_ITERATIONS ?= 200
BUILD_DIR := build
GENERATED_DIR := $(BUILD_DIR)/generated
C_GENERATED_DIR := $(GENERATED_DIR)/c
RUST_GENERATED_DIR := $(GENERATED_DIR)/rust
PYTHON_GENERATED_DIR := $(GENERATED_DIR)/python
SDL_FILES := $(wildcard sdl/*.sdl)

all: $(BUILD_DIR)/sdl_utest

test: test-c test-rust test-python

test-c: $(BUILD_DIR)/sdl_utest $(BUILD_DIR)/sdl_utest_le
	./$(BUILD_DIR)/sdl_utest
	./$(BUILD_DIR)/sdl_utest_le

test-rust: $(GENERATED_DIR)/.stamp
	$(CARGO) test --manifest-path tst/rust/Cargo.toml
	$(CARGO) test --manifest-path tst/rust/Cargo.toml --features wire-little-endian

test-python: $(GENERATED_DIR)/.stamp
	PYTHONPATH=runtime/python:$(PYTHON_GENERATED_DIR) $(PYTHON) -m unittest discover -s tst/python -v
	SDL_WIRE_ENDIAN=little PYTHONPATH=runtime/python:$(PYTHON_GENERATED_DIR) $(PYTHON) -m unittest discover -s tst/python -v

bench: bench-c bench-rust bench-python

bench-c: $(BUILD_DIR)/sdl_bench
	SDL_BENCH_ITERATIONS=$(BENCH_ITERATIONS) ./$(BUILD_DIR)/sdl_bench

bench-rust: $(GENERATED_DIR)/.stamp
	SDL_BENCH_ITERATIONS=$(BENCH_ITERATIONS) $(CARGO) run --release --manifest-path tst/rust/Cargo.toml --bin bench

bench-python: $(GENERATED_DIR)/.stamp
	SDL_BENCH_ITERATIONS=$(BENCH_ITERATIONS) PYTHONPATH=runtime/python:$(PYTHON_GENERATED_DIR) $(PYTHON) tst/python/bench.py

$(GENERATED_DIR)/.stamp: $(SDL_FILES) gen/generator.py gen/c_backend.py gen/rust_backend.py gen/python_backend.py Makefile
	mkdir -p $(GENERATED_DIR)
	$(PYTHON) gen/generator.py -c -rust -python sdl $(GENERATED_DIR)
	touch $@

$(C_GENERATED_DIR)/schema.h $(C_GENERATED_DIR)/codec_cases.h $(C_GENERATED_DIR)/benchmark.h $(C_GENERATED_DIR)/sdl_registry.h: $(GENERATED_DIR)/.stamp

$(BUILD_DIR)/sdl_utest: runtime/c/type_engine.c runtime/c/type_registry.c runtime/c/type_codec.c runtime/c/sdl_wire.c runtime/c/sdl_dynamic.c runtime/c/sdl_dynamic.h tst/main.c $(GENERATED_DIR)/.stamp $(C_GENERATED_DIR)/schema.h $(C_GENERATED_DIR)/codec_cases.h $(C_GENERATED_DIR)/benchmark.h $(C_GENERATED_DIR)/sdl_registry.h
	mkdir -p $(BUILD_DIR)
	$(CC) $(CFLAGS) -Iruntime/c -I$(C_GENERATED_DIR) runtime/c/type_engine.c runtime/c/type_registry.c runtime/c/type_codec.c runtime/c/sdl_wire.c runtime/c/sdl_dynamic.c $(C_GENERATED_DIR)/*.c tst/main.c -lm -o $@

$(BUILD_DIR)/sdl_utest_le: runtime/c/type_engine.c runtime/c/type_registry.c runtime/c/type_codec.c runtime/c/sdl_wire.c runtime/c/sdl_dynamic.c runtime/c/sdl_dynamic.h tst/main.c $(GENERATED_DIR)/.stamp $(C_GENERATED_DIR)/schema.h $(C_GENERATED_DIR)/codec_cases.h $(C_GENERATED_DIR)/benchmark.h $(C_GENERATED_DIR)/sdl_registry.h
	mkdir -p $(BUILD_DIR)
	$(CC) $(CFLAGS) -DSDL_WIRE_LITTLE_ENDIAN -Iruntime/c -I$(C_GENERATED_DIR) runtime/c/type_engine.c runtime/c/type_registry.c runtime/c/type_codec.c runtime/c/sdl_wire.c runtime/c/sdl_dynamic.c $(C_GENERATED_DIR)/*.c tst/main.c -lm -o $@

$(BUILD_DIR)/sdl_bench: runtime/c/type_engine.c runtime/c/type_registry.c runtime/c/type_codec.c runtime/c/sdl_wire.c runtime/c/sdl_dynamic.c runtime/c/sdl_dynamic.h tst/bench.c $(GENERATED_DIR)/.stamp $(C_GENERATED_DIR)/benchmark.h $(C_GENERATED_DIR)/sdl_registry.h
	mkdir -p $(BUILD_DIR)
	$(CC) $(CFLAGS) -Iruntime/c -I$(C_GENERATED_DIR) runtime/c/type_engine.c runtime/c/type_registry.c runtime/c/type_codec.c runtime/c/sdl_wire.c runtime/c/sdl_dynamic.c $(C_GENERATED_DIR)/*.c tst/bench.c -lm -o $@

clean:
	rm -rf $(BUILD_DIR)

.PHONY: all clean test test-c test-rust test-python bench bench-c bench-rust bench-python
