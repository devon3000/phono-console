#include "phono_audio/protocol.h"

#include <assert.h>
#include <stdint.h>
#include <stdio.h>

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
    puts("protocol test passed");
    return 0;
}
