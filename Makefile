PYTHON ?= python3
CC ?= gcc
CARGO ?= cargo
CFLAGS ?= -std=c99 -Wall -Wextra -pedantic -O3
GNAT_HOME ?= $(HOME)/opt/gnat-21.1
GNATMAKE ?= $(GNAT_HOME)/bin/gnatmake
ADA_RTS ?= sjlj
ADA_FLAGS ?= --RTS=$(ADA_RTS) -gnat2012 -gnata -gnatn -O3
ADA_GENERATED_DIR := build/generated/ada
ADA_C_SOURCES := $(wildcard runtime/c/*.c) runtime/ada/sdl_ada_bridge.c
ADA_C_OBJECTS := $(patsubst %.c,build/ada/%.o,$(ADA_C_SOURCES))
BENCH_ITERATIONS ?= 200
BUILD_DIR := build
GENERATED_DIR := $(BUILD_DIR)/generated
C_GENERATED_DIR := $(GENERATED_DIR)/c
RUST_GENERATED_DIR := $(GENERATED_DIR)/rust
PYTHON_GENERATED_DIR := $(GENERATED_DIR)/python
matlab_generated_dir := $(GENERATED_DIR)/matlab
OCTAVE ?= octave
matlab ?= matlab
MATLAB_FLAGS ?= -nodisplay -nosplash -nodesktop -nojvm
JAVAC ?= javac
JAVA ?= java
JAVA_GENERATED_DIR := $(GENERATED_DIR)/java
JAVA_TEST_CLASSES := $(BUILD_DIR)/java-test-classes
SDL_FILES := $(wildcard sdl/*.sdl)
CHECK_SILENT := $(findstring s,$(firstword $(MAKEFLAGS)))
RUN_TEST := $(if $(CHECK_SILENT),$(PYTHON) tst/run_quiet.py --,)
matlab_available := $(if $(strip $(matlab)),$(shell command -v $(matlab) >/dev/null 2>&1 && echo yes))
ifeq ($(matlab_available),yes)
matlab_bench_label := Matlab
# R2017a supports -r, not -batch; these codecs do not require the JVM.
matlab_run = $(matlab) $(MATLAB_FLAGS) -r "try, addpath('$(matlab_generated_dir)'); addpath('tst/matlab'); run_tests; catch err, disp(getReport(err, 'extended')); exit(1); end; exit(0)"
matlab_bench_run = $(matlab) $(MATLAB_FLAGS) -r "try, addpath('$(matlab_generated_dir)'); addpath('tst/matlab'); bench; catch err, disp(getReport(err, 'extended')); exit(1); end; exit(0)"
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
	@$(MAKE) --no-print-directory CHECK_SILENT= test-ada

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

python-native: $(GENERATED_DIR)/.stamp
	CC="$(CC)" $(PYTHON) tst/python/build_native.py "$(PYTHON_GENERATED_DIR)"

bench-python-buffers: $(GENERATED_DIR)/.stamp
	SDL_BENCH_ITERATIONS=$(BENCH_ITERATIONS) PYTHONPATH=runtime/python:$(PYTHON_GENERATED_DIR) $(PYTHON) tst/python/bench_buffers.py

bench-python: $(GENERATED_DIR)/.stamp
	SDL_BENCH_ITERATIONS=$(BENCH_ITERATIONS) PYTHONPATH=runtime/python:$(PYTHON_GENERATED_DIR) $(PYTHON) tst/python/bench.py

test-matlab: $(GENERATED_DIR)/.stamp
	$(RUN_TEST) env $(matlab_run)

test-java: $(GENERATED_DIR)/.stamp
	mkdir -p $(JAVA_TEST_CLASSES)
	$(JAVAC) -encoding UTF-8 -Xlint:-options -d $(JAVA_TEST_CLASSES) $(JAVA_GENERATED_DIR)/*.java tst/java/*.java
	$(RUN_TEST) env $(JAVA) -cp $(JAVA_TEST_CLASSES) SdlJavaTests

matlab-mex: $(GENERATED_DIR)/.stamp
	$(matlab) $(MATLAB_FLAGS) -r "try, addpath('tst/matlab'); build_mex('$(matlab_generated_dir)'); catch err, disp(getReport(err, 'extended')); exit(1); end; exit(0)"

bench-matlab: $(GENERATED_DIR)/.stamp
	SDL_BENCH_ITERATIONS=$(BENCH_ITERATIONS) $(matlab_bench_run)

bench-java: $(GENERATED_DIR)/.stamp
	mkdir -p $(JAVA_TEST_CLASSES)
	$(JAVAC) -encoding UTF-8 -Xlint:-options -d $(JAVA_TEST_CLASSES) $(JAVA_GENERATED_DIR)/*.java tst/java/*.java
	SDL_BENCH_ITERATIONS=$(BENCH_ITERATIONS) $(JAVA) -cp $(JAVA_TEST_CLASSES) SdlJavaBench

$(GENERATED_DIR)/.stamp: sdl $(SDL_FILES) gen/generator.py gen/c_backend.py gen/rust_backend.py gen/python_backend.py gen/matlab_backend.py gen/java_backend.py gen/ada_backend.py gen/ada_direct.py runtime/java/SdlCodec.java runtime/matlab/sdl_matlab_runtime.m runtime/matlab/sdl_mex_context.m Makefile
	mkdir -p $(GENERATED_DIR)
	$(PYTHON) gen/generator.py -c -rust -python -matlab -java -ada sdl $(GENERATED_DIR)
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

.PHONY: bench-python-buffers python-native matlab-mex all clean check test test-c test-rust test-python test-matlab test-java bench bench-c bench-rust bench-python bench-matlab bench-java

# GNAT's native runtime in the supplied installation lacks generic bodies.
# The complete sjlj runtime is used by default; ADA_RTS=native is supported.
build/ada/%.o: %.c $(wildcard runtime/c/*.h)
	mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -Iruntime/c -c $< -o $@

build/ada/sdl_ada_tests: tst/ada/run_tests.adb $(wildcard runtime/ada/*.ad?) $(GENERATED_DIR)/.stamp $(ADA_C_OBJECTS)
	mkdir -p build/ada
	PATH="$(GNAT_HOME)/bin:$(PATH)" $(GNATMAKE) $(ADA_FLAGS) -Iruntime/ada -I$(ADA_GENERATED_DIR) -D build/ada -o $@ $< -largs $(ADA_C_OBJECTS) -lm

build/ada/sdl_ada_bench: tst/ada/bench.adb $(wildcard runtime/ada/*.ad?) $(GENERATED_DIR)/.stamp $(ADA_C_OBJECTS)
	mkdir -p build/ada
	PATH="$(GNAT_HOME)/bin:$(PATH)" $(GNATMAKE) $(ADA_FLAGS) -Iruntime/ada -I$(ADA_GENERATED_DIR) -D build/ada -o $@ $< -largs $(ADA_C_OBJECTS) -lm

test-ada: build/ada/sdl_ada_tests
	$(RUN_TEST) ./build/ada/sdl_ada_tests

bench-ada: build/ada/sdl_ada_bench
	SDL_BENCH_ITERATIONS=$(BENCH_ITERATIONS) ./build/ada/sdl_ada_bench

.PHONY: test-ada bench-ada
