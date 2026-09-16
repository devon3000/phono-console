#define _POSIX_C_SOURCE 200809L

#include "phono_audio/probe.h"

#include <alsa/asoundlib.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

static int64_t timespec_us(const struct timespec *value) {
    return (int64_t)value->tv_sec * INT64_C(1000000) + value->tv_nsec / 1000;
}

static int64_t monotonic_us(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC_RAW, &now) != 0) return -1;
    return timespec_us(&now);
}

static int fail_alsa(const char *operation, int error) {
    fprintf(stderr, "%s: %s\n", operation, snd_strerror(error));
    return 1;
}

int phono_run_probe(const struct phono_probe_options *options) {
    snd_pcm_t *pcm = NULL;
    snd_pcm_hw_params_t *hw = NULL;
    snd_pcm_sw_params_t *sw = NULL;
    int error = snd_pcm_open(&pcm, options->device, SND_PCM_STREAM_CAPTURE, 0);
    if (error < 0) return fail_alsa("snd_pcm_open", error);

    snd_pcm_hw_params_alloca(&hw);
    if ((error = snd_pcm_hw_params_any(pcm, hw)) < 0 ||
        (error = snd_pcm_hw_params_set_access(
             pcm, hw, SND_PCM_ACCESS_RW_INTERLEAVED)) < 0 ||
        (error = snd_pcm_hw_params_set_format(pcm, hw, SND_PCM_FORMAT_S16_LE)) < 0 ||
        (error = snd_pcm_hw_params_set_channels(
             pcm, hw, options->requested_channels)) < 0) {
        snd_pcm_close(pcm);
        return fail_alsa("configure capture hardware", error);
    }

    unsigned int rate = options->requested_rate;
    int direction = 0;
    snd_pcm_uframes_t period = options->period_frames;
    if ((error = snd_pcm_hw_params_set_rate_near(pcm, hw, &rate, &direction)) < 0 ||
        (error = snd_pcm_hw_params_set_period_size_near(
             pcm, hw, &period, &direction)) < 0 ||
        (error = snd_pcm_hw_params(pcm, hw)) < 0) {
        snd_pcm_close(pcm);
        return fail_alsa("apply capture hardware parameters", error);
    }

    snd_pcm_uframes_t buffer_frames = 0;
    (void)snd_pcm_hw_params_get_buffer_size(hw, &buffer_frames);
    snd_pcm_sw_params_alloca(&sw);
    if ((error = snd_pcm_sw_params_current(pcm, sw)) < 0 ||
        (error = snd_pcm_sw_params_set_tstamp_mode(
             pcm, sw, SND_PCM_TSTAMP_ENABLE)) < 0 ||
        (error = snd_pcm_sw_params_set_tstamp_type(
             pcm, sw, SND_PCM_TSTAMP_TYPE_MONOTONIC_RAW)) < 0 ||
        (error = snd_pcm_sw_params_set_avail_min(pcm, sw, period)) < 0 ||
        (error = snd_pcm_sw_params(pcm, sw)) < 0 ||
        (error = snd_pcm_prepare(pcm)) < 0 ||
        (error = snd_pcm_start(pcm)) < 0) {
        snd_pcm_close(pcm);
        return fail_alsa("configure capture timestamps", error);
    }

    const size_t frame_bytes = options->requested_channels * sizeof(int16_t);
    int16_t *samples = calloc((size_t)period, frame_bytes);
    if (samples == NULL) {
        perror("calloc");
        snd_pcm_close(pcm);
        return 1;
    }

    printf("{\"event\":\"probe_started\",\"device\":\"%s\","
           "\"rate_hz\":%u,\"channels\":%u,\"period_frames\":%lu,"
           "\"buffer_frames\":%lu,\"clock\":\"monotonic_raw\"}\n",
           options->device, rate, options->requested_channels,
           (unsigned long)period, (unsigned long)buffer_frames);
    fflush(stdout);

    const int64_t finish_at = monotonic_us() + (int64_t)options->seconds * 1000000;
    uint64_t sequence = 0;
    uint64_t total_frames = 0;
    unsigned int xruns = 0;
    int64_t previous_first_us = 0;
    snd_pcm_sframes_t previous_frames = 0;

    while (monotonic_us() < finish_at) {
        error = snd_pcm_wait(pcm, 1000);
        if (error == 0) continue;
        if (error < 0) {
            error = snd_pcm_recover(pcm, error, 1);
            if (error < 0) break;
            xruns++;
            continue;
        }

        snd_pcm_uframes_t available_at_timestamp = 0;
        snd_htimestamp_t timestamp;
        error = snd_pcm_htimestamp(pcm, &available_at_timestamp, &timestamp);
        if (error < 0) break;
        snd_pcm_sframes_t frames = snd_pcm_readi(pcm, samples, period);
        if (frames < 0) {
            error = snd_pcm_recover(pcm, (int)frames, 1);
            if (error < 0) break;
            xruns++;
            continue;
        }
        if (frames == 0) continue;

        const int64_t hardware_us = timespec_us(&timestamp);
        const int64_t first_sample_us = hardware_us -
            (int64_t)available_at_timestamp * INT64_C(1000000) / rate;
        const int64_t expected_delta_us = sequence == 0 ? 0 :
            (int64_t)previous_frames * INT64_C(1000000) / rate;
        const int64_t actual_delta_us = sequence == 0 ? 0 :
            first_sample_us - previous_first_us;
        const int64_t delta_error_us = actual_delta_us - expected_delta_us;
        printf("{\"event\":\"capture\",\"sequence\":%" PRIu64
               ",\"frames\":%ld,\"available_frames\":%lu,"
               "\"hardware_time_us\":%" PRId64
               ",\"first_sample_time_us\":%" PRId64
               ",\"actual_delta_us\":%" PRId64
               ",\"expected_delta_us\":%" PRId64
               ",\"delta_error_us\":%" PRId64 ",\"xruns\":%u}\n",
               sequence, (long)frames, (unsigned long)available_at_timestamp,
               hardware_us, first_sample_us, actual_delta_us,
               expected_delta_us, delta_error_us, xruns);
        fflush(stdout);
        previous_first_us = first_sample_us;
        previous_frames = frames;
        total_frames += (uint64_t)frames;
        sequence++;
    }

    if (error < 0) fail_alsa("capture probe", error);
    printf("{\"event\":\"probe_finished\",\"blocks\":%" PRIu64
           ",\"frames\":%" PRIu64 ",\"xruns\":%u}\n",
           sequence, total_frames, xruns);
    free(samples);
    snd_pcm_close(pcm);
    return error < 0 ? 1 : 0;
}
