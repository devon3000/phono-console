#include "phono_audio/protocol.h"
#include "phono_audio/clock_estimator.h"

#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <math.h>
#include <stdlib.h>

int main(void) {
    const struct phono_audio_frame_header header = {
        .source = PHONO_AUDIO_SOURCE_BLUETOOTH,
        .flags = PHONO_AUDIO_FLAG_DISCONTINUITY,
        .sequence = UINT64_C(0x0102030405060708),
        .first_sample_time_us = INT64_C(123456789),
        .source_rate_hz = 44100,
        .output_rate_hz = 48000,
        .channels = 2,
        .frames = 960,
        .reported_transport_delay_us = 175000,
        .epoch = 7,
        .pcm_bytes = 3840,
    };
    uint8_t wire[PHONO_AUDIO_FRAME_HEADER_SIZE] = {0};
    phono_audio_encode_header(wire, &header);
    assert(phono_get_u32le(wire + 0) == PHONO_AUDIO_PROTOCOL_MAGIC);
    assert(phono_get_u16le(wire + 4) == PHONO_AUDIO_PROTOCOL_VERSION);
    assert(phono_get_u16le(wire + 6) == PHONO_AUDIO_FRAME_HEADER_SIZE);
    assert(phono_get_u16le(wire + 8) == PHONO_AUDIO_SOURCE_BLUETOOTH);
    assert(phono_get_u32le(wire + 12) == PHONO_AUDIO_FLAG_DISCONTINUITY);
    assert(phono_get_u64le(wire + 16) == header.sequence);
    assert((int64_t)phono_get_u64le(wire + 24) == header.first_sample_time_us);
    assert(phono_get_u32le(wire + 32) == 44100);
    assert(phono_get_u32le(wire + 36) == 48000);
    assert(phono_get_u16le(wire + 40) == 2);
    assert(phono_get_u16le(wire + 42) == 960);
    assert((int32_t)phono_get_u32le(wire + 44) == 175000);
    assert(phono_get_u32le(wire + 48) == 7);
    assert(phono_get_u32le(wire + 52) == 3840);

    struct phono_clock_estimator estimator;
    phono_clock_reset(&estimator);
    const long double actual_rate = 48001.5L;
    for (uint64_t index = 0; index < 1000; index++) {
        const uint64_t sample = index * 960U;
        const long double ideal = 2000000.0L +
            (long double)sample * 1000000.0L / actual_rate;
        uint64_t mixed = index + UINT64_C(0x9e3779b97f4a7c15);
        mixed = (mixed ^ (mixed >> 30)) * UINT64_C(0xbf58476d1ce4e5b9);
        mixed = (mixed ^ (mixed >> 27)) * UINT64_C(0x94d049bb133111eb);
        mixed ^= mixed >> 31;
        const int64_t jitter = (int64_t)(mixed % 30001U) - 15000;
        phono_clock_observe(&estimator, sample, (int64_t)llroundl(ideal) + jitter);
    }
    struct phono_clock_fit fit;
    assert(phono_clock_fit(&estimator, &fit));
    assert(fabsl(phono_clock_rate_hz(&fit) - actual_rate) < 5.0L);
    assert(fit.rms_residual_us > 8000 && fit.rms_residual_us < 10000);
    const int64_t predicted = phono_clock_timestamp(&fit, 960U * 1000U);
    const int64_t expected = (int64_t)llroundl(
        2000000.0L + 960000.0L * 1000000.0L / actual_rate
    );
    assert(llabs(predicted - expected) < 5000);

    struct phono_clock_mapper mapper;
    phono_clock_mapper_reset(&mapper);
    phono_clock_mapper_init(&mapper, 0, 1000000, 48000);
    const uint64_t update_sample = 48000;
    const int64_t before = phono_clock_mapper_timestamp(&mapper, update_sample);
    const struct phono_clock_fit faster_fit = {
        .us_per_sample = 1000000.0L / 48010.0L,
    };
    phono_clock_mapper_update(&mapper, &faster_fit, update_sample, 10.0L);
    const int64_t after = phono_clock_mapper_timestamp(&mapper, update_sample);
    assert(before == after);
    const long double mapped_rate = 1000000.0L / mapper.us_per_sample;
    assert(mapped_rate > 48000.0L && mapped_rate < 48000.6L);
    assert(phono_clock_mapper_timestamp(&mapper, update_sample + 960) > after);
    puts("protocol test passed");
    return 0;
}
