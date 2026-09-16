#define _POSIX_C_SOURCE 200809L

#include "phono_audio/engine.h"
#include "phono_audio/clock_estimator.h"
#include "phono_audio/protocol.h"

#include <alsa/asoundlib.h>
#include <alloca.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

static volatile sig_atomic_t running = 1;

static void stop_engine(int signal_number) {
    (void)signal_number;
    running = 0;
}

static int64_t timespec_us(const struct timespec *value) {
    return (int64_t)value->tv_sec * INT64_C(1000000) + value->tv_nsec / 1000;
}

static int fail_alsa(const char *operation, int error) {
    fprintf(stderr, "%s: %s\n", operation, snd_strerror(error));
    return -1;
}

static int open_capture(
    const struct phono_engine_options *options,
    snd_pcm_t **result,
    unsigned int *actual_rate,
    snd_pcm_uframes_t *actual_period
) {
    snd_pcm_t *pcm = NULL;
    snd_pcm_hw_params_t *hw = NULL;
    snd_pcm_sw_params_t *sw = NULL;
    int error = snd_pcm_open(&pcm, options->device, SND_PCM_STREAM_CAPTURE, 0);
    if (error < 0) return -1;
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
    snd_pcm_uframes_t buffer = options->buffer_frames;
    if ((error = snd_pcm_hw_params_set_rate_near(pcm, hw, &rate, &direction)) < 0 ||
        (error = snd_pcm_hw_params_set_period_size_near(
            pcm, hw, &period, &direction)) < 0 ||
        (error = snd_pcm_hw_params_set_buffer_size_near(pcm, hw, &buffer)) < 0 ||
        (error = snd_pcm_hw_params(pcm, hw)) < 0) {
        snd_pcm_close(pcm);
        return fail_alsa("apply capture hardware parameters", error);
    }
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
    *result = pcm;
    *actual_rate = rate;
    *actual_period = period;
    return 0;
}

static int capture_client(
    int client,
    const struct phono_engine_options *options,
    uint32_t *epoch,
    bool *opened
) {
    *opened = false;
    snd_pcm_t *pcm = NULL;
    unsigned int rate = 0;
    snd_pcm_uframes_t period = 0;
    if (open_capture(options, &pcm, &rate, &period) != 0) return 0;
    *opened = true;
    if (period > UINT16_MAX) {
        fprintf(stderr, "period exceeds protocol frame limit\n");
        snd_pcm_close(pcm);
        return -1;
    }
    const size_t pcm_bytes = (size_t)period * options->requested_channels * sizeof(int16_t);
    const size_t packet_bytes = PHONO_AUDIO_FRAME_HEADER_SIZE + pcm_bytes;
    uint8_t *packet = malloc(packet_bytes);
    if (packet == NULL) {
        perror("malloc");
        snd_pcm_close(pcm);
        return -1;
    }
    int flags = fcntl(client, F_GETFL, 0);
    if (flags >= 0) (void)fcntl(client, F_SETFL, flags | O_NONBLOCK);

    struct phono_clock_estimator estimator;
    struct phono_clock_mapper mapper;
    phono_clock_reset(&estimator);
    phono_clock_mapper_reset(&mapper);
    uint64_t sample_position = 0;
    uint64_t sequence = 0;
    uint32_t next_flags = PHONO_AUDIO_FLAG_DISCONTINUITY |
        PHONO_AUDIO_FLAG_CLOCK_RESET;
    unsigned int observations_since_fit = 0;
    bool client_gone = false;

    fprintf(stderr, "capture started: device=%s rate=%u period=%lu epoch=%u\n",
            options->device, rate, (unsigned long)period, *epoch);
    while (running) {
        int error = snd_pcm_wait(pcm, 1000);
        if (error == 0) continue;
        if (error < 0) {
            error = snd_pcm_recover(pcm, error, 1);
            if (error < 0) break;
            (*epoch)++;
            phono_clock_reset(&estimator);
            phono_clock_mapper_reset(&mapper);
            next_flags |= PHONO_AUDIO_FLAG_DISCONTINUITY |
                PHONO_AUDIO_FLAG_XRUN_RECOVERED |
                PHONO_AUDIO_FLAG_CLOCK_RESET;
            continue;
        }
        snd_pcm_uframes_t available = 0;
        snd_htimestamp_t hardware_time;
        error = snd_pcm_htimestamp(pcm, &available, &hardware_time);
        if (error < 0) break;
        snd_pcm_sframes_t frames = snd_pcm_readi(
            pcm, packet + PHONO_AUDIO_FRAME_HEADER_SIZE, period);
        if (frames < 0) {
            error = snd_pcm_recover(pcm, (int)frames, 1);
            if (error < 0) break;
            (*epoch)++;
            phono_clock_reset(&estimator);
            phono_clock_mapper_reset(&mapper);
            next_flags |= PHONO_AUDIO_FLAG_DISCONTINUITY |
                PHONO_AUDIO_FLAG_XRUN_RECOVERED |
                PHONO_AUDIO_FLAG_CLOCK_RESET;
            continue;
        }
        if (frames == 0) continue;

        const int64_t observed_first_us = timespec_us(&hardware_time) -
            (int64_t)available * INT64_C(1000000) / rate;
        phono_clock_observe(&estimator, sample_position, observed_first_us);
        if (!mapper.initialized) {
            phono_clock_mapper_init(
                &mapper, sample_position, observed_first_us, rate);
        }
        observations_since_fit++;
        if (estimator.count >= 50U && observations_since_fit >= 50U) {
            struct phono_clock_fit fit;
            if (phono_clock_fit(&estimator, &fit)) {
                phono_clock_mapper_update(&mapper, &fit, sample_position, 10.0L);
            }
            observations_since_fit = 0;
        }

        const size_t actual_pcm_bytes =
            (size_t)frames * options->requested_channels * sizeof(int16_t);
        const struct phono_audio_frame_header header = {
            .source = options->source,
            .flags = next_flags,
            .sequence = sequence,
            .first_sample_time_us =
                phono_clock_mapper_timestamp(&mapper, sample_position),
            .source_rate_hz = rate,
            .output_rate_hz = rate,
            .channels = (uint16_t)options->requested_channels,
            .frames = (uint16_t)frames,
            .reported_transport_delay_us = 0,
            .epoch = *epoch,
            .pcm_bytes = (uint32_t)actual_pcm_bytes,
        };
        phono_audio_encode_header(packet, &header);
        const ssize_t sent = send(
            client, packet, PHONO_AUDIO_FRAME_HEADER_SIZE + actual_pcm_bytes,
            MSG_NOSIGNAL | MSG_DONTWAIT);
        if (sent < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            next_flags |= PHONO_AUDIO_FLAG_DISCONTINUITY;
        } else if (sent < 0 || (size_t)sent !=
                   PHONO_AUDIO_FRAME_HEADER_SIZE + actual_pcm_bytes) {
            client_gone = true;
            break;
        } else {
            next_flags = 0;
        }
        sample_position += (uint64_t)frames;
        sequence++;
    }
    fprintf(stderr, "capture stopped: device=%s sequence=%" PRIu64 "\n",
            options->device, sequence);
    free(packet);
    snd_pcm_close(pcm);
    return client_gone ? 1 : 0;
}

static bool wait_for_capture_or_disconnect(int client) {
    struct pollfd descriptor = {
        .fd = client,
        .events = POLLHUP | POLLERR,
        .revents = 0,
    };
    int result;
    do {
        result = poll(&descriptor, 1, 1000);
    } while (result < 0 && errno == EINTR && running);
    if (!running) return false;
    if (result < 0) return false;
    return (descriptor.revents & (POLLHUP | POLLERR | POLLNVAL)) == 0;
}

int phono_run_engine(const struct phono_engine_options *options) {
    if (strlen(options->socket_path) >= sizeof(((struct sockaddr_un *)0)->sun_path)) {
        fprintf(stderr, "socket path is too long\n");
        return 1;
    }
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_handler = stop_engine;
    sigemptyset(&action.sa_mask);
    (void)sigaction(SIGINT, &action, NULL);
    (void)sigaction(SIGTERM, &action, NULL);

    int server = socket(AF_UNIX, SOCK_SEQPACKET, 0);
    if (server < 0) {
        perror("socket");
        return 1;
    }
    struct sockaddr_un address;
    memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    memcpy(address.sun_path, options->socket_path, strlen(options->socket_path) + 1U);
    (void)unlink(options->socket_path);
    if (bind(server, (struct sockaddr *)&address, sizeof(address)) < 0 ||
        chmod(options->socket_path, 0660) < 0 || listen(server, 1) < 0) {
        perror("bind/listen audio socket");
        close(server);
        (void)unlink(options->socket_path);
        return 1;
    }
    fprintf(stderr, "audio engine listening: %s\n", options->socket_path);
    uint32_t epoch = 1;
    while (running) {
        int client = accept(server, NULL, NULL);
        if (client < 0) {
            if (errno == EINTR) continue;
            perror("accept");
            break;
        }
        bool unavailable_logged = false;
        while (running) {
            bool opened = false;
            const int client_gone = capture_client(
                client, options, &epoch, &opened);
            if (client_gone) break;
            if (opened) {
                epoch++;
                unavailable_logged = false;
            } else if (!unavailable_logged) {
                fprintf(stderr,
                        "capture unavailable: waiting for Bluetooth audio\n");
                unavailable_logged = true;
            }
            if (!wait_for_capture_or_disconnect(client)) break;
        }
        close(client);
        epoch++;
    }
    close(server);
    (void)unlink(options->socket_path);
    return running ? 1 : 0;
}
