#include "phono_audio/clock_estimator.h"

#include <math.h>
#include <string.h>

void phono_clock_reset(struct phono_clock_estimator *estimator) {
    memset(estimator, 0, sizeof(*estimator));
}

void phono_clock_observe(
    struct phono_clock_estimator *estimator,
    uint64_t sample_position,
    int64_t timestamp_us
) {
    estimator->observations[estimator->next] =
        (struct phono_clock_observation){sample_position, timestamp_us};
    estimator->next = (estimator->next + 1U) % PHONO_CLOCK_OBSERVATIONS;
    if (estimator->count < PHONO_CLOCK_OBSERVATIONS) estimator->count++;
}

bool phono_clock_fit(
    const struct phono_clock_estimator *estimator,
    struct phono_clock_fit *fit
) {
    if (estimator->count < 2U) return false;
    long double mean_sample = 0;
    long double mean_time = 0;
    uint64_t first = UINT64_MAX;
    uint64_t last = 0;
    for (size_t index = 0; index < estimator->count; index++) {
        const struct phono_clock_observation *item =
            &estimator->observations[index];
        mean_sample += (long double)item->sample_position;
        mean_time += (long double)item->timestamp_us;
        if (item->sample_position < first) first = item->sample_position;
        if (item->sample_position > last) last = item->sample_position;
    }
    mean_sample /= (long double)estimator->count;
    mean_time /= (long double)estimator->count;

    long double covariance = 0;
    long double variance = 0;
    for (size_t index = 0; index < estimator->count; index++) {
        const struct phono_clock_observation *item =
            &estimator->observations[index];
        const long double sample_delta =
            (long double)item->sample_position - mean_sample;
        covariance += sample_delta * ((long double)item->timestamp_us - mean_time);
        variance += sample_delta * sample_delta;
    }
    if (variance == 0) return false;
    fit->us_per_sample = covariance / variance;
    fit->intercept_us = mean_time - fit->us_per_sample * mean_sample;
    fit->first_sample_position = first;
    fit->last_sample_position = last;
    fit->observations = estimator->count;

    long double residual_squares = 0;
    for (size_t index = 0; index < estimator->count; index++) {
        const struct phono_clock_observation *item =
            &estimator->observations[index];
        const long double predicted = fit->intercept_us +
            fit->us_per_sample * (long double)item->sample_position;
        const long double residual = (long double)item->timestamp_us - predicted;
        residual_squares += residual * residual;
    }
    fit->rms_residual_us = sqrtl(
        residual_squares / (long double)estimator->count
    );
    return fit->us_per_sample > 0;
}

int64_t phono_clock_timestamp(
    const struct phono_clock_fit *fit,
    uint64_t sample_position
) {
    return (int64_t)llroundl(
        fit->intercept_us + fit->us_per_sample * (long double)sample_position
    );
}

long double phono_clock_rate_hz(const struct phono_clock_fit *fit) {
    return 1000000.0L / fit->us_per_sample;
}
