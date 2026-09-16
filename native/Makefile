CC ?= cc
CPPFLAGS ?=
CFLAGS ?= -O2 -g -std=c17 -Wall -Wextra -Werror -Wpedantic
LDFLAGS ?=

ALSA_CFLAGS := $(shell pkg-config --cflags alsa 2>/dev/null)
ALSA_LIBS := $(shell pkg-config --libs alsa 2>/dev/null)

BUILD_DIR := build
PROBE := $(BUILD_DIR)/phono-audio-engine
PROTOCOL_TEST := $(BUILD_DIR)/test_protocol

.PHONY: all clean test

all: $(PROBE)

$(BUILD_DIR):
	mkdir -p $@

$(PROBE): src/main.c src/probe.c src/engine.c src/clock_estimator.c include/phono_audio/probe.h include/phono_audio/engine.h include/phono_audio/protocol.h include/phono_audio/clock_estimator.h | $(BUILD_DIR)
	@test -n "$(ALSA_LIBS)" || { echo "alsa development files not found (install libasound2-dev)" >&2; exit 1; }
	$(CC) $(CPPFLAGS) $(CFLAGS) $(ALSA_CFLAGS) -Iinclude src/main.c src/probe.c src/engine.c src/clock_estimator.c $(LDFLAGS) $(ALSA_LIBS) -lm -o $@

$(PROTOCOL_TEST): tests/test_protocol.c src/clock_estimator.c include/phono_audio/protocol.h include/phono_audio/clock_estimator.h | $(BUILD_DIR)
	$(CC) $(CPPFLAGS) $(CFLAGS) -Iinclude tests/test_protocol.c src/clock_estimator.c $(LDFLAGS) -lm -o $@

test: $(PROTOCOL_TEST)
	$(PROTOCOL_TEST)

clean:
	rm -rf $(BUILD_DIR)
