#ifndef PHONO_AUDIO_ENGINE_H
#define PHONO_AUDIO_ENGINE_H

#include <stdint.h>

struct phono_engine_options {
    const char *device;
    const char *socket_path;
    uint16_t source;
    unsigned int requested_rate;
    unsigned int requested_channels;
    unsigned int period_frames;
    unsigned int buffer_frames;
};

int phono_run_engine(const struct phono_engine_options *options);

#endif
