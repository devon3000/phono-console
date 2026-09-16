#ifndef PHONO_AUDIO_PROTOCOL_H
#define PHONO_AUDIO_PROTOCOL_H

#include <stdint.h>

#define PHONO_AUDIO_PROTOCOL_MAGIC UINT32_C(0x50434146) /* "PCAF" */
#define PHONO_AUDIO_PROTOCOL_VERSION UINT16_C(1)
#define PHONO_AUDIO_FRAME_HEADER_SIZE UINT16_C(56)

enum phono_audio_source {
    PHONO_AUDIO_SOURCE_PHONO = 1,
    PHONO_AUDIO_SOURCE_BLUETOOTH = 2,
    PHONO_AUDIO_SOURCE_MA_RETURN = 3,
};

enum phono_audio_frame_flags {
    PHONO_AUDIO_FLAG_DISCONTINUITY = 1U << 0,
    PHONO_AUDIO_FLAG_XRUN_RECOVERED = 1U << 1,
    PHONO_AUDIO_FLAG_CLOCK_RESET = 1U << 2,
    PHONO_AUDIO_FLAG_END_OF_STREAM = 1U << 3,
};

/*
 * Wire header fields are encoded little-endian explicitly. Do not send this
 * host struct directly: its layout and byte order are not the protocol.
 */
struct phono_audio_frame_header {
    uint16_t source;
    uint32_t flags;
    uint64_t sequence;
    int64_t first_sample_time_us;
    uint32_t source_rate_hz;
    uint32_t output_rate_hz;
    uint16_t channels;
    uint16_t frames;
    int32_t reported_transport_delay_us;
    uint32_t epoch;
    uint32_t pcm_bytes;
};

static inline void phono_put_u16le(uint8_t *out, uint16_t value) {
    out[0] = (uint8_t)(value & UINT16_C(0xff));
    out[1] = (uint8_t)(value >> 8);
}

static inline void phono_put_u32le(uint8_t *out, uint32_t value) {
    out[0] = (uint8_t)(value & UINT32_C(0xff));
    out[1] = (uint8_t)((value >> 8) & UINT32_C(0xff));
    out[2] = (uint8_t)((value >> 16) & UINT32_C(0xff));
    out[3] = (uint8_t)(value >> 24);
}

static inline void phono_put_u64le(uint8_t *out, uint64_t value) {
    for (unsigned int index = 0; index < 8; index++) {
        out[index] = (uint8_t)(value >> (index * 8));
    }
}

static inline uint16_t phono_get_u16le(const uint8_t *in) {
    return (uint16_t)((uint16_t)in[0] | ((uint16_t)in[1] << 8));
}

static inline uint32_t phono_get_u32le(const uint8_t *in) {
    return (uint32_t)in[0] | ((uint32_t)in[1] << 8) |
           ((uint32_t)in[2] << 16) | ((uint32_t)in[3] << 24);
}

static inline uint64_t phono_get_u64le(const uint8_t *in) {
    uint64_t value = 0;
    for (unsigned int index = 0; index < 8; index++) {
        value |= (uint64_t)in[index] << (index * 8);
    }
    return value;
}

static inline void phono_audio_encode_header(
    uint8_t out[PHONO_AUDIO_FRAME_HEADER_SIZE],
    const struct phono_audio_frame_header *header
) {
    phono_put_u32le(out + 0, PHONO_AUDIO_PROTOCOL_MAGIC);
    phono_put_u16le(out + 4, PHONO_AUDIO_PROTOCOL_VERSION);
    phono_put_u16le(out + 6, PHONO_AUDIO_FRAME_HEADER_SIZE);
    phono_put_u16le(out + 8, header->source);
    phono_put_u16le(out + 10, 0);
    phono_put_u32le(out + 12, header->flags);
    phono_put_u64le(out + 16, header->sequence);
    phono_put_u64le(out + 24, (uint64_t)header->first_sample_time_us);
    phono_put_u32le(out + 32, header->source_rate_hz);
    phono_put_u32le(out + 36, header->output_rate_hz);
    phono_put_u16le(out + 40, header->channels);
    phono_put_u16le(out + 42, header->frames);
    phono_put_u32le(out + 44, (uint32_t)header->reported_transport_delay_us);
    phono_put_u32le(out + 48, header->epoch);
    phono_put_u32le(out + 52, header->pcm_bytes);
}

#endif
