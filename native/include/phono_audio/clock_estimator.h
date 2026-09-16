#ifndef PHONO_AUDIO_CLOCK_ESTIMATOR_H
#define PHONO_AUDIO_CLOCK_ESTIMATOR_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define PHONO_CLOCK_OBSERVATIONS 2048U

struct phono_clock_observation {
    uint64_t sample_position;
    int64_t timestamp_us;
};

struct phono_clock_fit {
    long double us_per_sample;
    long double intercept_us;
    long double rms_residual_us;
    uint64_t first_sample_position;
    uint64_t last_sample_position;
    size_t observations;
};

struct phono_clock_estimator {
    struct phono_clock_observation observations[PHONO_CLOCK_OBSERVATIONS];
    size_t count;
    size_t next;
};

void phono_clock_reset(struct phono_clock_estimator *estimator);
void phono_clock_observe(
    struct phono_clock_estimator *estimator,
    uint64_t sample_position,
    int64_t timestamp_us
);
bool phono_clock_fit(
    const struct phono_clock_estimator *estimator,
    struct phono_clock_fit *fit
);
int64_t phono_clock_timestamp(
    const struct phono_clock_fit *fit,
    uint64_t sample_position
);
long double phono_clock_rate_hz(const struct phono_clock_fit *fit);

#endif
