#ifndef PHONO_AUDIO_PROBE_H
#define PHONO_AUDIO_PROBE_H

struct phono_probe_options {
    const char *device;
    unsigned int seconds;
    unsigned int requested_rate;
    unsigned int requested_channels;
    unsigned int period_frames;
};

int phono_run_probe(const struct phono_probe_options *options);

#endif
