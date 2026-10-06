PYTHON ?= python3
JAVAC ?= javac
JAVA ?= java
JAVA_SOURCE ?= 17
WIRE_ENDIAN ?= big

ROOT := ../..
BUILD_DIR := build
GENERATED_DIR := $(BUILD_DIR)/generated
CLASSES_DIR := $(BUILD_DIR)/classes
ifeq ($(WIRE_ENDIAN),little)
JAVA_ENDIAN := SDL_WIRE_ENDIAN=little
else ifneq ($(WIRE_ENDIAN),big)
$(error WIRE_ENDIAN must be big or little)
endif

$(GENERATED_DIR)/java/abc.java: generate

generate:
	mkdir -p $(GENERATED_DIR)
	$(PYTHON) $(ROOT)/gen/generator.py -java schema $(GENERATED_DIR)

$(CLASSES_DIR)/.stamp: generate java/Main.java $(ROOT)/runtime/java/SdlCodec.java
	mkdir -p $(CLASSES_DIR)
	$(JAVAC) -source $(JAVA_SOURCE) -target $(JAVA_SOURCE) -Xlint:-options -d $(CLASSES_DIR) $(GENERATED_DIR)/java/*.java java/Main.java
	cp $(ROOT)/runtime/java/SdlCodec.java $(CLASSES_DIR)/
	touch $@

build: $(CLASSES_DIR)/.stamp

run: build
	$(JAVA_ENDIAN) $(JAVA) -cp $(CLASSES_DIR) Main

clean:
	rm -rf $(BUILD_DIR)

.PHONY: generate build run clean
