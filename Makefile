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
matlab_generated_dir := $(GENERATED_DIR)/matlab
OCTAVE ?= octave
matlab ?= matlab
JAVAC ?= javac
JAVA ?= java
JAVA_SOURCE ?= 8
JAVA_GENERATED_DIR := $(GENERATED_DIR)/java
JAVA_TEST_CLASSES := $(BUILD_DIR)/java-test-classes
SDL_FILES := $(wildcard sdl/*.sdl)
CHECK_SILENT := $(findstring s,$(firstword $(MAKEFLAGS)))
RUN_TEST := $(if $(CHECK_SILENT),$(PYTHON) tst/run_quiet.py --,)
matlab_available := $(shell command -v $(matlab) >/dev/null 2>&1 && echo yes)
ifeq ($(matlab_available),yes)
matlab_bench_label := Matlab
matlab_run = $(matlab) -nodisplay -nosplash -nodesktop -r "try, addpath('$(matlab_generated_dir)'); addpath('tst/matlab'); run_tests; catch err, disp(getReport(err, 'extended')); exit(1); end; exit(0)"
matlab_bench_run = $(matlab) -nodisplay -nosplash -nodesktop -r "try, addpath('$(matlab_generated_dir)'); addpath('tst/matlab'); bench; catch err, disp(getReport(err, 'extended')); exit(1); end; exit(0)"
else
matlab_bench_label := Octave
matlab_run = $(OCTAVE) --quiet --no-gui --eval "addpath('$(matlab_generated_dir)'); addpath('tst/matlab'); run_tests"
matlab_bench_run = $(OCTAVE) --quiet --no-gui --eval "addpath('$(matlab_generated_dir)'); addpath('tst/matlab'); bench"
endif

all: $(BUILD_DIR)/sdl_utest

check:
	@$(PYTHON) tst/run_check.py make

test:
	@$(MAKE) --no-print-directory CHECK_SILENT= test-c
	@$(MAKE) --no-print-directory CHECK_SILENT= test-rust
	@$(MAKE) --no-print-directory CHECK_SILENT= test-python
	@$(MAKE) --no-print-directory CHECK_SILENT= test-matlab
	@$(MAKE) --no-print-directory CHECK_SILENT= test-java

test-c: $(BUILD_DIR)/sdl_utest
	@if [ -z "$(CHECK_SILENT)" ]; then echo "=== Component C: generated typed codec, runtime storage, then wire limits ==="; fi
	$(RUN_TEST) ./$(BUILD_DIR)/sdl_utest

test-rust: $(GENERATED_DIR)/.stamp
	@if [ -z "$(CHECK_SILENT)" ]; then echo "=== Component Rust: codec, malformed input, then limits (serial order) ==="; fi
	$(RUN_TEST) env RUST_TEST_THREADS=1 $(CARGO) test --manifest-path tst/rust/Cargo.toml -- --test-threads=1

test-python: $(GENERATED_DIR)/.stamp
	@if [ -z "$(CHECK_SILENT)" ]; then echo "=== Component Python: runtime, schema/codegen, invalid input, limits ==="; fi
	$(RUN_TEST) env PYTHONPATH=runtime/python:$(PYTHON_GENERATED_DIR) $(PYTHON) tst/python/run_tests.py

bench: $(GENERATED_DIR)/.stamp
	@$(PYTHON) tst/run_benchmarks.py make "$(BENCH_ITERATIONS)" "$(matlab_bench_label)"

bench-c: $(BUILD_DIR)/sdl_bench
	SDL_BENCH_ITERATIONS=$(BENCH_ITERATIONS) ./$(BUILD_DIR)/sdl_bench

bench-rust: $(GENERATED_DIR)/.stamp
	SDL_BENCH_ITERATIONS=$(BENCH_ITERATIONS) $(CARGO) run --release --manifest-path tst/rust/Cargo.toml --bin bench

bench-python: $(GENERATED_DIR)/.stamp
	SDL_BENCH_ITERATIONS=$(BENCH_ITERATIONS) PYTHONPATH=runtime/python:$(PYTHON_GENERATED_DIR) $(PYTHON) tst/python/bench.py

test-matlab: $(GENERATED_DIR)/.stamp
	$(RUN_TEST) env $(matlab_run)

test-java: $(GENERATED_DIR)/.stamp
	mkdir -p $(JAVA_TEST_CLASSES)
	$(JAVAC) -encoding UTF-8 -source $(JAVA_SOURCE) -target $(JAVA_SOURCE) -Xlint:-options -d $(JAVA_TEST_CLASSES) $(JAVA_GENERATED_DIR)/*.java tst/java/*.java
	$(RUN_TEST) env $(JAVA) -cp $(JAVA_TEST_CLASSES) SdlJavaTests

bench-matlab: $(GENERATED_DIR)/.stamp
	SDL_BENCH_ITERATIONS=$(BENCH_ITERATIONS) $(matlab_bench_run)

bench-java: $(GENERATED_DIR)/.stamp
	mkdir -p $(JAVA_TEST_CLASSES)
	$(JAVAC) -encoding UTF-8 -source $(JAVA_SOURCE) -target $(JAVA_SOURCE) -Xlint:-options -d $(JAVA_TEST_CLASSES) $(JAVA_GENERATED_DIR)/*.java tst/java/*.java
	SDL_BENCH_ITERATIONS=$(BENCH_ITERATIONS) $(JAVA) -cp $(JAVA_TEST_CLASSES) SdlJavaBench

$(GENERATED_DIR)/.stamp: sdl $(SDL_FILES) gen/generator.py gen/c_backend.py gen/rust_backend.py gen/python_backend.py gen/matlab_backend.py gen/java_backend.py runtime/java/SdlCodec.java runtime/matlab/sdl_matlab_runtime.m Makefile
	mkdir -p $(GENERATED_DIR)
	$(PYTHON) gen/generator.py -c -rust -python -matlab -java sdl $(GENERATED_DIR)
	touch $@

$(C_GENERATED_DIR)/schema.h $(C_GENERATED_DIR)/codec_cases.h $(C_GENERATED_DIR)/benchmark.h $(C_GENERATED_DIR)/empty_message.h $(C_GENERATED_DIR)/sdl_registry.h: $(GENERATED_DIR)/.stamp

$(BUILD_DIR)/sdl_utest: runtime/c/type_engine.c runtime/c/type_registry.c runtime/c/type_codec.c runtime/c/sdl_wire.c runtime/c/sdl_dynamic.c runtime/c/type_display.c runtime/c/sdl_dynamic.h runtime/c/sdl_context.h tst/main.c $(GENERATED_DIR)/.stamp $(C_GENERATED_DIR)/schema.h $(C_GENERATED_DIR)/codec_cases.h $(C_GENERATED_DIR)/benchmark.h $(C_GENERATED_DIR)/empty_message.h $(C_GENERATED_DIR)/sdl_registry.h
	mkdir -p $(BUILD_DIR)
	$(CC) $(CFLAGS) -Iruntime/c -I$(C_GENERATED_DIR) runtime/c/type_engine.c runtime/c/type_registry.c runtime/c/type_codec.c runtime/c/sdl_wire.c runtime/c/sdl_dynamic.c runtime/c/type_display.c $(C_GENERATED_DIR)/*.c tst/main.c -lm -o $@

$(BUILD_DIR)/sdl_bench: runtime/c/type_engine.c runtime/c/type_registry.c runtime/c/type_codec.c runtime/c/sdl_wire.c runtime/c/sdl_dynamic.c runtime/c/type_display.c runtime/c/sdl_dynamic.h runtime/c/sdl_context.h tst/bench.c $(GENERATED_DIR)/.stamp $(C_GENERATED_DIR)/benchmark.h $(C_GENERATED_DIR)/sdl_registry.h
	mkdir -p $(BUILD_DIR)
	$(CC) $(CFLAGS) -Iruntime/c -I$(C_GENERATED_DIR) runtime/c/type_engine.c runtime/c/type_registry.c runtime/c/type_codec.c runtime/c/sdl_wire.c runtime/c/sdl_dynamic.c runtime/c/type_display.c $(C_GENERATED_DIR)/*.c tst/bench.c -lm -o $@

clean:
	rm -rf $(BUILD_DIR)

.PHONY: all clean check test test-c test-rust test-python test-matlab test-java bench bench-c bench-rust bench-python bench-matlab bench-java
