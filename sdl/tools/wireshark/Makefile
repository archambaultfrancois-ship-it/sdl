CC ?= cc
PKG_CONFIG ?= pkg-config
CFLAGS ?= -std=c11 -O3 -Wall -Wextra
WIRESHARK_CFLAGS := $(shell $(PKG_CONFIG) --cflags wireshark 2>/dev/null)
WIRESHARK_LIBS := $(shell $(PKG_CONFIG) --libs wireshark 2>/dev/null)
PLUGIN_SUFFIX ?= .so
TARGET := sdl$(PLUGIN_SUFFIX)

ROOT := ../..
RUNTIME := $(wildcard $(ROOT)/runtime/c/*.c)
ADAPTER := sdl-recording.c sdl-recording.h

all: $(TARGET)

$(TARGET): packet-sdl.c $(ADAPTER) $(RUNTIME) $(wildcard $(ROOT)/runtime/c/*.h)
	@$(PKG_CONFIG) --exists wireshark || { echo "Wireshark development headers/libraries are required (pkg-config wireshark)." >&2; exit 1; }
	$(CC) $(CFLAGS) $(WIRESHARK_CFLAGS) -I$(ROOT)/runtime/c -fPIC -shared $(LDFLAGS) -o $@ packet-sdl.c sdl-recording.c $(RUNTIME) $(WIRESHARK_LIBS) -lm

clean:
	rm -f $(TARGET)

.PHONY: all clean
