PYTHON ?= python3
CC ?= gcc
CFLAGS ?= -std=c99 -Wall -Wextra -pedantic -O2
BUILD_DIR := build
GENERATED_DIR := $(BUILD_DIR)/generated

all: $(BUILD_DIR)/sdl_demo

test: $(BUILD_DIR)/sdl_demo
	./$(BUILD_DIR)/sdl_demo

$(GENERATED_DIR)/.stamp: tst/schema.sdl gen/generator.py
	mkdir -p $(GENERATED_DIR)
	$(PYTHON) gen/generator.py tst/schema.sdl $(GENERATED_DIR)
	touch $@

$(GENERATED_DIR)/schema.h $(GENERATED_DIR)/schema.c: $(GENERATED_DIR)/.stamp

$(BUILD_DIR)/sdl_demo: runtime/c/type_engine.c runtime/c/type_registry.c runtime/c/type_codec.c runtime/c/sdl_wire.c tst/main.c $(GENERATED_DIR)/schema.c $(GENERATED_DIR)/schema.h
	mkdir -p $(BUILD_DIR)
	$(CC) $(CFLAGS) -Iruntime/c -I$(GENERATED_DIR) runtime/c/type_engine.c runtime/c/type_registry.c runtime/c/type_codec.c runtime/c/sdl_wire.c $(GENERATED_DIR)/schema.c tst/main.c -lm -o $@

clean:
	rm -rf $(BUILD_DIR)

.PHONY: all clean test
