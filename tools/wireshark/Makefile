CC ?= cc
PKG_CONFIG ?= pkg-config
CFLAGS ?= -O2 -Wall -Wextra
WIRESHARK_CFLAGS := $(shell $(PKG_CONFIG) --cflags wireshark 2>/dev/null)
WIRESHARK_LIBS := $(shell $(PKG_CONFIG) --libs wireshark 2>/dev/null)
WIRESHARK_VERSION := $(shell $(PKG_CONFIG) --modversion wireshark 2>/dev/null)
WIRESHARK_VERSION_PARTS := $(subst ., ,$(WIRESHARK_VERSION))
ifneq ($(WIRESHARK_VERSION),)
WIRESHARK_CFLAGS += -DWIRESHARK_VERSION_MAJOR=$(word 1,$(WIRESHARK_VERSION_PARTS)) \
	-DWIRESHARK_VERSION_MINOR=$(word 2,$(WIRESHARK_VERSION_PARTS))
endif
PLUGIN_SUFFIX ?= .so
TARGET := sdl$(PLUGIN_SUFFIX)

all: $(TARGET)

$(TARGET): packet-sdl.c
	$(CC) $(CFLAGS) $(WIRESHARK_CFLAGS) -fPIC -shared -o $@ $< $(WIRESHARK_LIBS)

clean:
	rm -f $(TARGET)

.PHONY: all clean
