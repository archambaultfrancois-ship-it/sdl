PYTHON ?= python3
CC ?= gcc
CARGO ?= cargo
CFLAGS ?= -std=c99 -Wall -Wextra -pedantic -O3
GNAT_HOME ?= $(HOME)/opt/gnat-21.1
GNATMAKE ?= $(GNAT_HOME)/bin/gnatmake
ADA_RTS ?= sjlj
ADA_FLAGS ?= $(if $(strip $(ADA_RTS)),--RTS=$(ADA_RTS)) -gnat2012 -gnata -gnatn -O3
ADA_GENERATED_DIR := build/generated/ada
ADA_C_SOURCES := $(wildcard runtime/c/*.c) runtime/ada/sdl_ada_bridge.c
ADA_C_OBJECTS := $(patsubst %.c,build/ada/%.o,$(ADA_C_SOURCES))
BENCH_ITERATIONS ?= 200
BUILD_DIR := build
GENERATED_DIR := $(BUILD_DIR)/generated
C_GENERATED_DIR := $(GENERATED_DIR)/c
C_RUNTIME_HEADERS := $(wildcard runtime/c/*.h)
C_GENERATED_HEADERS := $(shell find $(C_GENERATED_DIR) -type f -name '*.h' 2>/dev/null)
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
SDL_FILES := $(shell find schemas -type f \( -name '*.sdl' -o -name '*.sdl.json' -o -name '*.sdl.yaml' -o -name '*.sdl.yml' -o -name '*.sdl.xsd' \))
SDL_DIRS := $(shell find schemas -type d)
CHECK_SILENT := $(findstring s,$(firstword $(MAKEFLAGS)))
RUN_TEST := $(if $(CHECK_SILENT),$(PYTHON) tests/run_quiet.py --,)
matlab_available := $(if $(strip $(matlab)),$(shell command -v $(matlab) >/dev/null 2>&1 && echo yes))
ifeq ($(matlab_available),yes)
matlab_bench_label := Matlab
# R2017a supports -r, not -batch; these codecs do not require the JVM.
matlab_run = $(matlab) $(MATLAB_FLAGS) -r "try, addpath('$(matlab_generated_dir)'); addpath('tests/matlab'); run_tests; catch err, disp(getReport(err, 'extended')); exit(1); end; exit(0)"
matlab_bench_run = $(matlab) $(MATLAB_FLAGS) -r "try, addpath('$(matlab_generated_dir)'); addpath('tests/matlab'); bench; catch err, disp(getReport(err, 'extended')); exit(1); end; exit(0)"
else
matlab_bench_label := Octave
matlab_run = $(OCTAVE) --quiet --no-gui --eval "addpath('$(matlab_generated_dir)'); addpath('tests/matlab'); run_tests"
matlab_bench_run = $(OCTAVE) --quiet --no-gui --eval "addpath('$(matlab_generated_dir)'); addpath('tests/matlab'); bench"
endif

all: $(BUILD_DIR)/sdl_utest

check:
	@$(PYTHON) tests/run_check.py make

test:
	@$(MAKE) --no-print-directory CHECK_SILENT= test-c
	@$(MAKE) --no-print-directory CHECK_SILENT= test-rust
	@$(MAKE) --no-print-directory CHECK_SILENT= test-python
	@$(MAKE) --no-print-directory CHECK_SILENT= test-matlab
	@$(MAKE) --no-print-directory CHECK_SILENT= test-java
	@$(MAKE) --no-print-directory CHECK_SILENT= test-ada
	@$(MAKE) --no-print-directory CHECK_SILENT= test-representation
	@$(MAKE) --no-print-directory CHECK_SILENT= test-packages

test-c: $(BUILD_DIR)/sdl_utest
	@if [ -z "$(CHECK_SILENT)" ]; then echo "=== Component C: generated typed codec, runtime storage, then wire limits ==="; fi
	$(RUN_TEST) ./$(BUILD_DIR)/sdl_utest

test-rust: $(GENERATED_DIR)/.stamp
	@if [ -z "$(CHECK_SILENT)" ]; then echo "=== Component Rust: codec, malformed input, then limits (serial order) ==="; fi
	$(RUN_TEST) env RUST_TEST_THREADS=1 $(CARGO) test --manifest-path tests/rust/Cargo.toml -- --test-threads=1

test-python: $(GENERATED_DIR)/.stamp
	@if [ -z "$(CHECK_SILENT)" ]; then echo "=== Component Python: runtime, schema/codegen, invalid input, limits ==="; fi
	$(RUN_TEST) env PYTHONPATH=runtime/python:$(PYTHON_GENERATED_DIR) $(PYTHON) tests/python/run_tests.py

# Optional readers and an independent XSD validator. Core tests need neither.
SCHEMA_DEPS ?= build/schema-deps
schema-deps:
	$(PYTHON) -m pip --disable-pip-version-check install --upgrade --target "$(SCHEMA_DEPS)" -r tests/requirements-schema-formats.txt

test-schema-formats:
	$(RUN_TEST) env PYTHONPATH="$(SCHEMA_DEPS):runtime/python" $(PYTHON) tests/python/test_schema_formats.py

.PHONY: schema-deps test-schema-formats

# Optional catalogue predicate service and native language gateways.
filter-native:
	CARGO="$(CARGO)" CC="$(CC)" $(PYTHON) tools/filter/build_native.py

filter-python:
	CARGO="$(CARGO)" CC="$(CC)" $(PYTHON) tools/filter/build_native.py --python

filter-jni:
	CARGO="$(CARGO)" CC="$(CC)" JAVAC="$(JAVAC)" $(PYTHON) tools/filter/build_native.py --jni

filter-mex:
	CARGO="$(CARGO)" CC="$(CC)" $(PYTHON) tools/filter/build_native.py --mex

filter-matlab-mex: filter-native
	env CCACHE_DISABLE=1 $(matlab) $(MATLAB_FLAGS) -r "try, addpath('runtime/filter/matlab'); build_filter_mex(pwd); catch err, disp(getReport(err, 'extended')); exit(1); end; exit(0)"

filter-bindings:
	CARGO="$(CARGO)" CC="$(CC)" JAVAC="$(JAVAC)" $(PYTHON) tools/filter/build_native.py --python --jni --mex

test-filter: filter-bindings
	$(RUN_TEST) $(CARGO) test --offline --manifest-path runtime/rust/Cargo.toml --target-dir build/filter
	$(RUN_TEST) $(CARGO) test --locked --offline --manifest-path runtime/filter/Cargo.toml --target-dir build/filter
	$(RUN_TEST) env CC="$(CC)" JAVAC="$(JAVAC)" JAVA="$(JAVA)" OCTAVE="$(OCTAVE)" $(PYTHON) tests/filter/run_tests.py --gnatmake "$(GNATMAKE)"

bench-filter: filter-bindings
	CC="$(CC)" JAVAC="$(JAVAC)" JAVA="$(JAVA)" OCTAVE="$(OCTAVE)" $(PYTHON) tests/filter/run_benchmarks.py --no-build --gnatmake "$(GNATMAKE)"

.PHONY: filter-native filter-python filter-jni filter-mex filter-matlab-mex filter-bindings test-filter bench-filter

# Optional recording tools do not add dependencies to the core runtime tests.
RECORDING_DEPS ?= build/recording-deps
recording-deps:
	$(PYTHON) -m pip --disable-pip-version-check install --upgrade --target "$(RECORDING_DEPS)" --no-deps -r tools/recording/requirements.txt

test-recording:
	PYTHONPATH=$(RECORDING_DEPS):runtime/python $(PYTHON) -m unittest discover -s tests/recording -v

test-wireshark-adapter:
	PYTHONPATH=$(RECORDING_DEPS):runtime/python CC="$(CC)" $(PYTHON) -m unittest discover -s tests/recording -p test_dissector.py -v

.PHONY: test-wireshark-tshark
test-wireshark-tshark:
	PYTHONPATH=$(RECORDING_DEPS):runtime/python SDL_TEST_TSHARK=1 $(PYTHON) -m unittest discover -s tests/recording -p test_tshark.py -v

recording-native:
	CARGO="$(CARGO)" CC="$(CC)" $(PYTHON) tools/recording/build_native.py

recording-jni:
	CARGO="$(CARGO)" CC="$(CC)" JAVAC="$(JAVAC)" $(PYTHON) tools/recording/build_native.py --jni

recording-mex:
	CARGO="$(CARGO)" CC="$(CC)" $(PYTHON) tools/recording/build_native.py --mex

test-recording-native: recording-native
	$(CARGO) test --locked --manifest-path runtime/recording/Cargo.toml --target-dir build/recording
	PYTHONPATH=$(RECORDING_DEPS):runtime/python CC="$(CC)" $(PYTHON) -m unittest discover -s tests/recording -p test_native.py -v
	PYTHONPATH=$(RECORDING_DEPS):runtime/python $(PYTHON) -m unittest discover -s tests/recording -p test_pcapng.py -v

test-recording-jni: recording-jni
	PYTHONPATH=$(RECORDING_DEPS):runtime/python JAVAC="$(JAVAC)" JAVA="$(JAVA)" $(PYTHON) tests/recording/run_gateways.py --jni

test-recording-mex: recording-mex
	PYTHONPATH=$(RECORDING_DEPS):runtime/python OCTAVE="$(OCTAVE)" $(PYTHON) tests/recording/run_gateways.py --mex

# Optional GNAT binding depends only on standard Ada packages and the C ABI.
# Override RECORDING_GNATMAKE / RECORDING_ADA_FLAGS for a native GNAT installation.
RECORDING_GNATMAKE ?= $(GNATMAKE)
RECORDING_ADA_FLAGS ?= $(ADA_FLAGS)
test-recording-ada: recording-native
	mkdir -p build/recording/ada
	$(RECORDING_GNATMAKE) $(RECORDING_ADA_FLAGS) -Iruntime/recording/ada -D build/recording/ada -o build/recording/ada/test tests/recording/native/recording_ada_tests.adb -largs -L$(CURDIR)/build/recording/release -lsdl_recording -Wl,-rpath,$(CURDIR)/build/recording/release
	$(PYTHON) tests/recording/run_ada.py

.PHONY: recording-deps test-recording test-wireshark-adapter recording-native recording-jni recording-mex test-recording-native test-recording-jni test-recording-mex test-recording-ada

bench: $(GENERATED_DIR)/.stamp
	@$(PYTHON) tests/run_benchmarks.py make "$(BENCH_ITERATIONS)" "$(matlab_bench_label)"

bench-c: $(BUILD_DIR)/sdl_bench
	SDL_BENCH_ITERATIONS=$(BENCH_ITERATIONS) ./$(BUILD_DIR)/sdl_bench

bench-rust: $(GENERATED_DIR)/.stamp
	SDL_BENCH_ITERATIONS=$(BENCH_ITERATIONS) $(CARGO) run --release --manifest-path tests/rust/Cargo.toml --bin bench

sdl-python-native: $(GENERATED_DIR)/.stamp
	CC="$(CC)" $(PYTHON) tests/python/build_native.py "$(PYTHON_GENERATED_DIR)"

bench-python-buffers: $(GENERATED_DIR)/.stamp
	SDL_BENCH_ITERATIONS=$(BENCH_ITERATIONS) PYTHONPATH=runtime/python:$(PYTHON_GENERATED_DIR) $(PYTHON) tests/python/bench_buffers.py

bench-python: $(GENERATED_DIR)/.stamp
	SDL_BENCH_ITERATIONS=$(BENCH_ITERATIONS) PYTHONPATH=runtime/python:$(PYTHON_GENERATED_DIR) $(PYTHON) tests/python/bench.py

bench-python-fixed: $(GENERATED_DIR)/.stamp
	PYTHONPATH=runtime/python:$(PYTHON_GENERATED_DIR) $(PYTHON) tests/python/bench_fixed.py

test-matlab: $(GENERATED_DIR)/.stamp
	$(RUN_TEST) env $(matlab_run)

test-java: $(GENERATED_DIR)/.stamp
	mkdir -p $(JAVA_TEST_CLASSES)
	$(JAVAC) -encoding UTF-8 -Xlint:-options -d $(JAVA_TEST_CLASSES) $$(find $(JAVA_GENERATED_DIR) -name '*.java') tests/java/*.java
	$(RUN_TEST) env $(JAVA) -cp $(JAVA_TEST_CLASSES) SdlJavaTests

sld-matlab-example: $(GENERATED_DIR)/.stamp
	$(matlab) $(MATLAB_FLAGS) -r "try, addpath('tests/matlab'); build_mex('$(matlab_generated_dir)'); catch err, disp(getReport(err, 'extended')); exit(1); end; exit(0)"

bench-matlab: $(GENERATED_DIR)/.stamp
	SDL_BENCH_ITERATIONS=$(BENCH_ITERATIONS) $(matlab_bench_run)

bench-matlab-fixed: $(GENERATED_DIR)/.stamp
ifeq ($(matlab_available),yes)
	$(matlab) $(MATLAB_FLAGS) -r "try, addpath('$(matlab_generated_dir)'); addpath('tests/matlab'); bench_fixed; catch err, disp(getReport(err, 'extended')); exit(1); end; exit(0)"
else
	$(OCTAVE) --quiet --no-gui --eval "addpath('$(matlab_generated_dir)'); addpath('tests/matlab'); bench_fixed"
endif

bench-java: $(GENERATED_DIR)/.stamp
	mkdir -p $(JAVA_TEST_CLASSES)
	$(JAVAC) -encoding UTF-8 -Xlint:-options -d $(JAVA_TEST_CLASSES) $$(find $(JAVA_GENERATED_DIR) -name '*.java') tests/java/*.java
	SDL_BENCH_ITERATIONS=$(BENCH_ITERATIONS) $(JAVA) -cp $(JAVA_TEST_CLASSES) SdlJavaBench

$(GENERATED_DIR)/.stamp: $(SDL_DIRS) $(SDL_FILES) sdlc/generator.py sdlc/schema_formats.py sdlc/c_backend.py sdlc/rust_backend.py sdlc/python_backend.py sdlc/matlab_backend.py sdlc/java_backend.py sdlc/ada_backend.py sdlc/ada_filter.py sdlc/ada_direct.py runtime/java/SdlCodec.java runtime/matlab/sdl_matlab_runtime.m runtime/matlab/sdl_mex_context.m runtime/matlab/sdl_representation.m Makefile
	mkdir -p $(GENERATED_DIR)
	$(PYTHON) sdlc/generator.py -c -rust -python -matlab -java -ada schemas $(GENERATED_DIR)
	touch $@

$(C_GENERATED_DIR)/schema.h $(C_GENERATED_DIR)/codec_cases.h $(C_GENERATED_DIR)/benchmark.h $(C_GENERATED_DIR)/empty_message.h $(C_GENERATED_DIR)/sdl_registry.h: $(GENERATED_DIR)/.stamp

$(BUILD_DIR)/sdl_utest: runtime/c/sdl_representation.inc runtime/c/type_engine.c runtime/c/type_registry.c runtime/c/type_codec.c runtime/c/sdl_wire.c runtime/c/sdl_dynamic.c runtime/c/type_display.c $(C_RUNTIME_HEADERS) $(C_GENERATED_HEADERS) tests/main.c $(GENERATED_DIR)/.stamp $(C_GENERATED_DIR)/schema.h $(C_GENERATED_DIR)/codec_cases.h $(C_GENERATED_DIR)/benchmark.h $(C_GENERATED_DIR)/empty_message.h $(C_GENERATED_DIR)/sdl_registry.h
	mkdir -p $(BUILD_DIR)
	$(CC) $(CFLAGS) -Iruntime/c -I$(C_GENERATED_DIR) runtime/c/type_engine.c runtime/c/type_registry.c runtime/c/type_codec.c runtime/c/sdl_wire.c runtime/c/sdl_dynamic.c runtime/c/type_display.c $$(find $(C_GENERATED_DIR) -name '*.c') tests/main.c -lm -o $@

$(BUILD_DIR)/sdl_bench: runtime/c/sdl_representation.inc runtime/c/type_engine.c runtime/c/type_registry.c runtime/c/type_codec.c runtime/c/sdl_wire.c runtime/c/sdl_dynamic.c runtime/c/type_display.c $(C_RUNTIME_HEADERS) $(C_GENERATED_HEADERS) tests/bench.c $(GENERATED_DIR)/.stamp $(C_GENERATED_DIR)/benchmark.h $(C_GENERATED_DIR)/sdl_registry.h
	mkdir -p $(BUILD_DIR)
	$(CC) $(CFLAGS) -Iruntime/c -I$(C_GENERATED_DIR) runtime/c/type_engine.c runtime/c/type_registry.c runtime/c/type_codec.c runtime/c/sdl_wire.c runtime/c/sdl_dynamic.c runtime/c/type_display.c $$(find $(C_GENERATED_DIR) -name '*.c') tests/bench.c -lm -o $@

clean:
	rm -rf $(BUILD_DIR)

.PHONY: bench-python-buffers sdl-python-native sld-matlab-example all clean check test test-c test-rust test-python test-matlab test-java bench bench-c bench-rust bench-python bench-matlab bench-java

# GNAT's native runtime in the supplied installation lacks generic bodies.
# The complete sjlj runtime is used by default; ADA_RTS=native is supported.
build/ada/%.o: %.c $(wildcard runtime/c/*.h) runtime/c/sdl_representation.inc
	mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -Iruntime/c -c $< -o $@

build/ada/sdl_ada_tests: tests/ada/run_tests.adb $(wildcard runtime/ada/*.ad?) $(GENERATED_DIR)/.stamp $(ADA_C_OBJECTS)
	mkdir -p build/ada
	PATH="$(GNAT_HOME)/bin:$(PATH)" $(GNATMAKE) $(ADA_FLAGS) -Iruntime/ada -I$(ADA_GENERATED_DIR) -D build/ada -o $@ $< -largs $(ADA_C_OBJECTS) -lm

build/ada/sdl_ada_bench: tests/ada/bench.adb $(wildcard runtime/ada/*.ad?) $(GENERATED_DIR)/.stamp $(ADA_C_OBJECTS)
	mkdir -p build/ada
	PATH="$(GNAT_HOME)/bin:$(PATH)" $(GNATMAKE) $(ADA_FLAGS) -Iruntime/ada -I$(ADA_GENERATED_DIR) -D build/ada -o $@ $< -largs $(ADA_C_OBJECTS) -lm

test-ada: build/ada/sdl_ada_tests
	$(RUN_TEST) ./build/ada/sdl_ada_tests

bench-ada: build/ada/sdl_ada_bench
	SDL_BENCH_ITERATIONS=$(BENCH_ITERATIONS) ./build/ada/sdl_ada_bench

build/ada/sdl_ada_bench_fixed: tests/ada/bench_fixed.adb $(wildcard runtime/ada/*.ad?) $(GENERATED_DIR)/.stamp $(ADA_C_OBJECTS)
	mkdir -p build/ada
	PATH="$(GNAT_HOME)/bin:$(PATH)" $(GNATMAKE) $(ADA_FLAGS) -Iruntime/ada -I$(ADA_GENERATED_DIR) -D build/ada -o $@ $< -largs $(ADA_C_OBJECTS) -lm

bench-ada-fixed: build/ada/sdl_ada_bench_fixed
	./build/ada/sdl_ada_bench_fixed

.PHONY: test-ada bench-ada bench-ada-fixed bench-matlab-fixed bench-python-fixed

.PHONY: test-representation
test-representation:
	$(RUN_TEST) $(PYTHON) tests/run_representation.py --gnatmake "$(GNATMAKE)" $(if $(matlab_available),--matlab "$(matlab)")

.PHONY: test-packages
test-packages:
	$(RUN_TEST) $(PYTHON) tests/run_packages.py --gnatmake "$(GNATMAKE)" $(if $(matlab_available),--matlab "$(matlab)")
