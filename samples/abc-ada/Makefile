PYTHON ?= python3
CC ?= cc
CFLAGS ?= -std=c99 -Wall -Wextra -pedantic -O3
GNAT_HOME ?= $(HOME)/opt/gnat-21.1
GNATMAKE ?= $(GNAT_HOME)/bin/gnatmake
ADA_RTS ?= sjlj
ADA_FLAGS ?= --RTS=$(ADA_RTS) -gnat2012 -gnata -gnatn -O3

ROOT := ../..
BUILD_DIR := build
GENERATED_DIR := $(BUILD_DIR)/generated
ADA_GENERATED_DIR := $(GENERATED_DIR)/ada
BINARY := $(BUILD_DIR)/abc-ada
C_RUNTIME := type_engine type_registry type_codec sdl_wire sdl_dynamic type_display
C_OBJECTS := $(addprefix $(BUILD_DIR)/c/,$(addsuffix .o,$(C_RUNTIME))) $(BUILD_DIR)/c/sdl_ada_bridge.o

all: $(BINARY)

generate: $(GENERATED_DIR)/.stamp

$(GENERATED_DIR)/.stamp: schema/abc.sdl $(ROOT)/gen/generator.py $(ROOT)/gen/ada_backend.py $(ROOT)/gen/ada_direct.py
	mkdir -p $(GENERATED_DIR)
	$(PYTHON) $(ROOT)/gen/generator.py -ada schema $(GENERATED_DIR)
	touch $@

$(BUILD_DIR)/c/%.o: $(ROOT)/runtime/c/%.c $(wildcard $(ROOT)/runtime/c/*.h)
	mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -I$(ROOT)/runtime/c -c $< -o $@

$(BUILD_DIR)/c/sdl_ada_bridge.o: $(ROOT)/runtime/ada/sdl_ada_bridge.c $(wildcard $(ROOT)/runtime/c/*.h)
	mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -I$(ROOT)/runtime/c -c $< -o $@

$(BINARY): $(GENERATED_DIR)/.stamp $(wildcard ada/*.ad?) $(wildcard $(ROOT)/runtime/ada/*.ad?) $(C_OBJECTS)
	mkdir -p $(BUILD_DIR)/ada
	PATH="$(GNAT_HOME)/bin:$(PATH)" $(GNATMAKE) $(ADA_FLAGS) -Iada -I$(ROOT)/runtime/ada -I$(ADA_GENERATED_DIR) -D $(BUILD_DIR)/ada -o $@ ada/main.adb -largs $(C_OBJECTS) -lm

run: $(BINARY)
	./$(BINARY)

clean:
	rm -rf $(BUILD_DIR)

.PHONY: all generate run clean
