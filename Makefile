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
MATLAB_GENERATED_DIR := $(GENERATED_DIR)/matlab
OCTAVE ?= octave
JAVAC ?= javac
JAVA ?= java
JAVA_SOURCE ?= 8
JAVA_GENERATED_DIR := $(GENERATED_DIR)/java
JAVA_TEST_CLASSES := $(BUILD_DIR)/java-test-classes
SDL_FILES := $(wildcard sdl/*.sdl)

all: $(BUILD_DIR)/sdl_utest

check test:
	$(MAKE) test-c
	$(MAKE) test-rust
	$(MAKE) test-python
	$(MAKE) test-matlab
	$(MAKE) test-java

test-c: $(BUILD_DIR)/sdl_utest $(BUILD_DIR)/sdl_utest_le
	@echo "=== Component C: generated typed codec, runtime storage, then wire limits ==="
	./$(BUILD_DIR)/sdl_utest
	@echo "=== Component C, little endian ==="
	./$(BUILD_DIR)/sdl_utest_le

test-rust: $(GENERATED_DIR)/.stamp
	@echo "=== Component Rust: codec, malformed input, then limits (serial order) ==="
	RUST_TEST_THREADS=1 $(CARGO) test --manifest-path tst/rust/Cargo.toml -- --test-threads=1
	@echo "=== Component Rust, little endian ==="
	RUST_TEST_THREADS=1 $(CARGO) test --manifest-path tst/rust/Cargo.toml --features wire-little-endian -- --test-threads=1

test-python: $(GENERATED_DIR)/.stamp
	@echo "=== Component Python: runtime, schema/codegen, invalid input, limits ==="
	PYTHONPATH=runtime/python:$(PYTHON_GENERATED_DIR) $(PYTHON) tst/python/run_tests.py
	@echo "=== Component Python, little endian ==="
	SDL_WIRE_ENDIAN=little PYTHONPATH=runtime/python:$(PYTHON_GENERATED_DIR) $(PYTHON) tst/python/run_tests.py

bench: bench-c bench-rust bench-python
	$(MAKE) bench-matlab
	$(MAKE) bench-java

bench-c: $(BUILD_DIR)/sdl_bench
	SDL_BENCH_ITERATIONS=$(BENCH_ITERATIONS) ./$(BUILD_DIR)/sdl_bench

bench-rust: $(GENERATED_DIR)/.stamp
	SDL_BENCH_ITERATIONS=$(BENCH_ITERATIONS) $(CARGO) run --release --manifest-path tst/rust/Cargo.toml --bin bench

bench-python: $(GENERATED_DIR)/.stamp
	SDL_WIRE_ENDIAN=big SDL_BENCH_ITERATIONS=$(BENCH_ITERATIONS) PYTHONPATH=runtime/python:$(PYTHON_GENERATED_DIR) $(PYTHON) tst/python/bench.py

test-matlab: $(GENERATED_DIR)/.stamp
	SDL_WIRE_ENDIAN=big $(OCTAVE) --quiet --no-gui --eval "addpath('$(MATLAB_GENERATED_DIR)'); addpath('tst/matlab'); run_tests"
	SDL_WIRE_ENDIAN=little $(OCTAVE) --quiet --no-gui --eval "addpath('$(MATLAB_GENERATED_DIR)'); addpath('tst/matlab'); run_tests"

test-java: $(GENERATED_DIR)/.stamp
	mkdir -p $(JAVA_TEST_CLASSES)
	$(JAVAC) -source $(JAVA_SOURCE) -target $(JAVA_SOURCE) -Xlint:-options -d $(JAVA_TEST_CLASSES) $(JAVA_GENERATED_DIR)/*.java tst/java/*.java
	SDL_WIRE_ENDIAN=big $(JAVA) -cp $(JAVA_TEST_CLASSES) SdlJavaTests
	SDL_WIRE_ENDIAN=little $(JAVA) -cp $(JAVA_TEST_CLASSES) SdlJavaTests

bench-matlab: $(GENERATED_DIR)/.stamp
	SDL_BENCH_ITERATIONS=$(BENCH_ITERATIONS) SDL_WIRE_ENDIAN=big $(OCTAVE) --quiet --no-gui --eval "addpath('$(MATLAB_GENERATED_DIR)'); addpath('tst/matlab'); bench"

bench-java: $(GENERATED_DIR)/.stamp
	mkdir -p $(JAVA_TEST_CLASSES)
	$(JAVAC) -source $(JAVA_SOURCE) -target $(JAVA_SOURCE) -Xlint:-options -d $(JAVA_TEST_CLASSES) $(JAVA_GENERATED_DIR)/*.java tst/java/*.java
	SDL_WIRE_ENDIAN=big SDL_BENCH_ITERATIONS=$(BENCH_ITERATIONS) $(JAVA) -cp $(JAVA_TEST_CLASSES) SdlJavaBench

$(GENERATED_DIR)/.stamp: sdl $(SDL_FILES) gen/generator.py gen/c_backend.py gen/rust_backend.py gen/python_backend.py gen/matlab_backend.py gen/java_backend.py runtime/java/SdlCodec.java Makefile
	mkdir -p $(GENERATED_DIR)
	$(PYTHON) gen/generator.py -c -rust -python -matlab -java sdl $(GENERATED_DIR)
	touch $@

$(C_GENERATED_DIR)/schema.h $(C_GENERATED_DIR)/codec_cases.h $(C_GENERATED_DIR)/benchmark.h $(C_GENERATED_DIR)/empty_message.h $(C_GENERATED_DIR)/sdl_registry.h: $(GENERATED_DIR)/.stamp

$(BUILD_DIR)/sdl_utest: runtime/c/type_engine.c runtime/c/type_registry.c runtime/c/type_codec.c runtime/c/sdl_wire.c runtime/c/sdl_dynamic.c runtime/c/type_display.c runtime/c/sdl_dynamic.h tst/main.c $(GENERATED_DIR)/.stamp $(C_GENERATED_DIR)/schema.h $(C_GENERATED_DIR)/codec_cases.h $(C_GENERATED_DIR)/benchmark.h $(C_GENERATED_DIR)/empty_message.h $(C_GENERATED_DIR)/sdl_registry.h
	mkdir -p $(BUILD_DIR)
	$(CC) $(CFLAGS) -Iruntime/c -I$(C_GENERATED_DIR) runtime/c/type_engine.c runtime/c/type_registry.c runtime/c/type_codec.c runtime/c/sdl_wire.c runtime/c/sdl_dynamic.c runtime/c/type_display.c $(C_GENERATED_DIR)/*.c tst/main.c -lm -o $@

$(BUILD_DIR)/sdl_utest_le: runtime/c/type_engine.c runtime/c/type_registry.c runtime/c/type_codec.c runtime/c/sdl_wire.c runtime/c/sdl_dynamic.c runtime/c/type_display.c runtime/c/sdl_dynamic.h tst/main.c $(GENERATED_DIR)/.stamp $(C_GENERATED_DIR)/schema.h $(C_GENERATED_DIR)/codec_cases.h $(C_GENERATED_DIR)/benchmark.h $(C_GENERATED_DIR)/empty_message.h $(C_GENERATED_DIR)/sdl_registry.h
	mkdir -p $(BUILD_DIR)
	$(CC) $(CFLAGS) -DSDL_WIRE_LITTLE_ENDIAN -Iruntime/c -I$(C_GENERATED_DIR) runtime/c/type_engine.c runtime/c/type_registry.c runtime/c/type_codec.c runtime/c/sdl_wire.c runtime/c/sdl_dynamic.c runtime/c/type_display.c $(C_GENERATED_DIR)/*.c tst/main.c -lm -o $@

$(BUILD_DIR)/sdl_bench: runtime/c/type_engine.c runtime/c/type_registry.c runtime/c/type_codec.c runtime/c/sdl_wire.c runtime/c/sdl_dynamic.c runtime/c/type_display.c runtime/c/sdl_dynamic.h tst/bench.c $(GENERATED_DIR)/.stamp $(C_GENERATED_DIR)/benchmark.h $(C_GENERATED_DIR)/sdl_registry.h
	mkdir -p $(BUILD_DIR)
	$(CC) $(CFLAGS) -Iruntime/c -I$(C_GENERATED_DIR) runtime/c/type_engine.c runtime/c/type_registry.c runtime/c/type_codec.c runtime/c/sdl_wire.c runtime/c/sdl_dynamic.c runtime/c/type_display.c $(C_GENERATED_DIR)/*.c tst/bench.c -lm -o $@

clean:
	rm -rf $(BUILD_DIR)

.PHONY: all clean test test-c test-rust test-python test-matlab test-java bench bench-c bench-rust bench-python bench-matlab bench-java
