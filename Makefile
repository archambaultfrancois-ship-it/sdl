PYTHON ?= python3
CC ?= gcc
CFLAGS ?= -std=c99 -Wall -Wextra -pedantic -O2
BUILD_DIR := build
GENERATED_DIR := $(BUILD_DIR)/generated
SDL_FILES := $(wildcard sdl/*.sdl)

all: $(BUILD_DIR)/sdl_demo

test: $(BUILD_DIR)/sdl_demo
	./$(BUILD_DIR)/sdl_demo

$(GENERATED_DIR)/.stamp: $(SDL_FILES) gen/generator.py Makefile
	mkdir -p $(GENERATED_DIR)
	$(PYTHON) gen/generator.py sdl $(GENERATED_DIR)
	touch $@

$(GENERATED_DIR)/schema.h $(GENERATED_DIR)/codec_cases.h $(GENERATED_DIR)/sdl_registry.h: $(GENERATED_DIR)/.stamp

$(BUILD_DIR)/sdl_demo: runtime/c/type_engine.c runtime/c/type_registry.c runtime/c/type_codec.c runtime/c/sdl_wire.c tst/main.c $(GENERATED_DIR)/.stamp $(GENERATED_DIR)/schema.h $(GENERATED_DIR)/codec_cases.h $(GENERATED_DIR)/sdl_registry.h
	mkdir -p $(BUILD_DIR)
	$(CC) $(CFLAGS) -Iruntime/c -I$(GENERATED_DIR) runtime/c/type_engine.c runtime/c/type_registry.c runtime/c/type_codec.c runtime/c/sdl_wire.c $(GENERATED_DIR)/*.c tst/main.c -lm -o $@

clean:
	rm -rf $(BUILD_DIR)

.PHONY: all clean test
