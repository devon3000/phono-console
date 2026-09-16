#include "phono_audio/probe.h"

#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void usage(FILE *stream) {
    fprintf(stream,
            "usage: phono-audio-engine probe --device PCM [--seconds N] "
            "[--rate HZ] [--channels N] [--period-frames N] "
            "[--buffer-frames N]\n");
}

static int parse_unsigned(const char *text, unsigned int *result) {
    char *end = NULL;
    errno = 0;
    unsigned long value = strtoul(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || value > UINT_MAX) {
        return -1;
    }
    *result = (unsigned int)value;
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2 || strcmp(argv[1], "probe") != 0) {
        usage(stderr);
        return 2;
    }
    struct phono_probe_options options = {
        .device = NULL,
        .seconds = 30,
        .requested_rate = 48000,
        .requested_channels = 2,
        .period_frames = 960,
        .buffer_frames = 3840,
    };
    for (int index = 2; index < argc; index++) {
        if (index + 1 >= argc) {
            usage(stderr);
            return 2;
        }
        const char *name = argv[index++];
        const char *value = argv[index];
        if (strcmp(name, "--device") == 0) {
            options.device = value;
        } else if (strcmp(name, "--seconds") == 0) {
            if (parse_unsigned(value, &options.seconds) != 0) return 2;
        } else if (strcmp(name, "--rate") == 0) {
            if (parse_unsigned(value, &options.requested_rate) != 0) return 2;
        } else if (strcmp(name, "--channels") == 0) {
            if (parse_unsigned(value, &options.requested_channels) != 0) return 2;
        } else if (strcmp(name, "--period-frames") == 0) {
            if (parse_unsigned(value, &options.period_frames) != 0) return 2;
        } else if (strcmp(name, "--buffer-frames") == 0) {
            if (parse_unsigned(value, &options.buffer_frames) != 0) return 2;
        } else {
            usage(stderr);
            return 2;
        }
    }
    if (options.device == NULL || options.seconds == 0 ||
        options.requested_rate == 0 || options.requested_channels == 0 ||
        options.period_frames == 0 ||
        options.buffer_frames < options.period_frames * 2) {
        usage(stderr);
        return 2;
    }
    return phono_run_probe(&options);
}
