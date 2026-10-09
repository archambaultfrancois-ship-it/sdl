PYTHON ?= python3
CC ?= cc
CFLAGS ?= -std=c99 -Wall -Wextra -pedantic -O3

ROOT := ../..
BUILD_DIR := build
GENERATED_DIR := $(BUILD_DIR)/generated
C_GENERATED_DIR := $(GENERATED_DIR)/c
BINARY := $(BUILD_DIR)/abc-c
C_RUNTIME := $(ROOT)/runtime/c/type_engine.c $(ROOT)/runtime/c/type_registry.c \
	$(ROOT)/runtime/c/type_codec.c $(ROOT)/runtime/c/sdl_wire.c \
	$(ROOT)/runtime/c/sdl_dynamic.c $(ROOT)/runtime/c/type_display.c

generate:
	mkdir -p $(GENERATED_DIR)
	$(PYTHON) $(ROOT)/gen/generator.py -c schema $(GENERATED_DIR)

$(BINARY): generate c/main.c
	mkdir -p $(BUILD_DIR)
	$(CC) $(CFLAGS) -pthread -I$(ROOT)/runtime/c -I$(C_GENERATED_DIR) \
		$(C_RUNTIME) $(C_GENERATED_DIR)/*.c c/main.c -lm -o $@

run: $(BINARY)
	./$(BINARY)

clean:
	rm -rf $(BUILD_DIR)

.PHONY: generate run clean
