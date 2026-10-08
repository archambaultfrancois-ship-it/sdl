PYTHON ?= python3
CC ?= cc
CARGO ?= cargo
CFLAGS ?= -std=c99 -Wall -Wextra -pedantic -O2
INDENT_WIDTH ?= 3

ROOT := ../..
BUILD_DIR := build
GENERATED_DIR := $(BUILD_DIR)/generated
C_GENERATED_DIR := $(GENERATED_DIR)/c
C_BINARY := $(BUILD_DIR)/radio_capture_c

PYTHON_ENV := PYTHONPATH=$(ROOT)/runtime/python:$(CURDIR)/$(GENERATED_DIR)/python
C_RUNTIME := $(ROOT)/runtime/c/type_engine.c $(ROOT)/runtime/c/type_registry.c \
	$(ROOT)/runtime/c/type_codec.c $(ROOT)/runtime/c/sdl_wire.c \
	$(ROOT)/runtime/c/sdl_dynamic.c $(ROOT)/runtime/c/type_display.c

generate:
	$(PYTHON) $(ROOT)/gen/generator.py -c -rust -python schema $(GENERATED_DIR)

$(C_BINARY): generate c/main.c
	mkdir -p $(BUILD_DIR)
	$(CC) $(CFLAGS) -I$(ROOT)/runtime/c -I$(C_GENERATED_DIR) \
		$(C_RUNTIME) $(C_GENERATED_DIR)/*.c c/main.c -lm -o $@

c: $(C_BINARY)

rust: generate
	$(CARGO) build --manifest-path rust/Cargo.toml

python: generate
	$(PYTHON_ENV) $(PYTHON) python/main.py roundtrip $(INDENT_WIDTH)

demo: c rust python
	./$(C_BINARY) roundtrip $(INDENT_WIDTH)
	$(CARGO) run --manifest-path rust/Cargo.toml -- roundtrip $(INDENT_WIDTH)

interop: $(C_BINARY) rust generate
	./$(C_BINARY) encode $(BUILD_DIR)/from-c.sdlw
	$(CARGO) run --manifest-path rust/Cargo.toml -- decode $(BUILD_DIR)/from-c.sdlw
	$(PYTHON_ENV) $(PYTHON) python/main.py decode $(BUILD_DIR)/from-c.sdlw
	$(CARGO) run --manifest-path rust/Cargo.toml -- encode $(BUILD_DIR)/from-rust.sdlw
	./$(C_BINARY) decode $(BUILD_DIR)/from-rust.sdlw
	$(PYTHON_ENV) $(PYTHON) python/main.py decode $(BUILD_DIR)/from-rust.sdlw
	$(PYTHON_ENV) $(PYTHON) python/main.py encode $(BUILD_DIR)/from-python.sdlw
	./$(C_BINARY) decode $(BUILD_DIR)/from-python.sdlw
	$(CARGO) run --manifest-path rust/Cargo.toml -- decode $(BUILD_DIR)/from-python.sdlw
	cmp $(BUILD_DIR)/from-c.sdlw $(BUILD_DIR)/from-rust.sdlw
	cmp $(BUILD_DIR)/from-c.sdlw $(BUILD_DIR)/from-python.sdlw

clean:
	rm -rf $(BUILD_DIR) rust/target

.PHONY: generate c rust python demo interop clean
