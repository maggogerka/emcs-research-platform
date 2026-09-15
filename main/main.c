#include <inttypes.h>
#include <math.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "driver/uart.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

#define I2C_SDA_GPIO              GPIO_NUM_8
#define I2C_SCL_GPIO              GPIO_NUM_9
#define I2C_FREQUENCY_HZ          100000
#define I2C_FIRST_ADDRESS         0x08
#define I2C_LAST_ADDRESS          0x77
#define I2C_PROBE_TIMEOUT_MS      50
#define I2C_TRANSFER_TIMEOUT_MS   100

#define ADS1115_ADDRESS           0x48
#define ADS1115_REG_CONVERSION    0x00
#define ADS1115_REG_CONFIG        0x01
#define ADS1115_CONFIG_EMG        0x42E3
#define ADS1115_VOLTS_PER_BIT     0.000125f
#define ADS_SAMPLE_PERIOD_US      1163
#define ADS_SAMPLE_RATE_HZ        860

#define MPU6050_ADDRESS           0x68
#define MPU6050_REG_SMPLRT_DIV    0x19
#define MPU6050_REG_CONFIG        0x1A
#define MPU6050_REG_GYRO_CONFIG   0x1B
#define MPU6050_REG_ACCEL_CONFIG  0x1C
#define MPU6050_REG_ACCEL_XOUT_H  0x3B
#define MPU6050_REG_PWR_MGMT_1    0x6B
#define MPU6050_REG_WHO_AM_I      0x75
#define MPU6050_WHO_AM_I_VALUE    0x68
#define MPU6050_ACCEL_LSB_PER_G   16384.0f
#define MPU6050_GYRO_LSB_PER_DPS  131.0f
#define MPU_SAMPLE_PERIOD_MS      10
#define MPU_GYRO_CAL_SAMPLES      200

#define AD8232_LO_MINUS_GPIO      GPIO_NUM_4
#define AD8232_LO_PLUS_GPIO       GPIO_NUM_5
#define SERIAL_BAUD_RATE          460800

#define RMS_WINDOW_SAMPLES        43
#define CALIBRATION_SAMPLES       (ADS_SAMPLE_RATE_HZ * 10)
#define ADAPTIVE_HIST_BINS        4096
#define ADAPTIVE_BIN_WIDTH        0.25f
#define ADAPTIVE_WINDOW_SAMPLES   (ADS_SAMPLE_RATE_HZ * 10)
#define ADAPTIVE_UPDATE_SAMPLES   ADS_SAMPLE_RATE_HZ
#define DC_TRACKING_ALPHA         0.001162f
#define CONTRACTION_CONFIRM_US    50000ULL
#define RELEASE_CONFIRM_US        100000ULL
#define REFRACTORY_US             350000ULL

#define FRAME_MAGIC               0xA55A
#define FRAME_VERSION             1
#define FRAME_MAX_PAYLOAD         320
#define TX_QUEUE_DEPTH            16
#define EMG_SAMPLES_PER_PACKET    32
#define IMU_SAMPLES_PER_PACKET    10

enum {
    FRAME_STATUS = 1,
    FRAME_EMG_BATCH = 2,
    FRAME_IMU_BATCH = 3,
    FRAME_EVENT = 4,
    FRAME_ERROR = 5,
    FRAME_ACK = 6,
};

enum {
    EVENT_CONTRACTION_START = 1,
    EVENT_CONTRACTION_RELEASE = 2,
    EVENT_EMG_CALIBRATION_DONE = 3,
    EVENT_IMU_CALIBRATION_DONE = 4,
    EVENT_LEADS_CHANGED = 5,
};

typedef enum {
    DETECTOR_LEADS_OFF = 0,
    DETECTOR_CALIBRATING = 1,
    DETECTOR_REST = 2,
    DETECTOR_CANDIDATE = 3,
    DETECTOR_ACTIVE = 4,
    DETECTOR_REFRACTORY = 5,
} detector_state_t;

typedef struct __attribute__((packed)) {
    uint16_t magic;
    uint8_t version;
    uint8_t type;
    uint16_t payload_length;
    uint32_t sequence;
    uint64_t timestamp_us;
} frame_header_t;

typedef struct {
    uint8_t type;
    uint16_t payload_length;
    uint64_t timestamp_us;
    uint8_t payload[FRAME_MAX_PAYLOAD];
} tx_packet_t;

typedef struct __attribute__((packed)) {
    int16_t raw;
    float envelope;
    uint8_t flags;
    uint8_t timer_gap;
} emg_wire_sample_t;

typedef struct __attribute__((packed)) {
    uint32_t first_sample_index;
    uint16_t sample_count;
    uint16_t dropped_samples;
    float adaptive_on;
    float adaptive_off;
    float fixed_on;
    float fixed_off;
    uint8_t detector_state;
    uint8_t leads;
    uint8_t motion;
    uint8_t recording;
    emg_wire_sample_t samples[EMG_SAMPLES_PER_PACKET];
} emg_wire_packet_t;

typedef struct __attribute__((packed)) {
    int16_t ax;
    int16_t ay;
    int16_t az;
    int16_t gx;
    int16_t gy;
    int16_t gz;
    uint8_t timer_gap;
} imu_wire_sample_t;

typedef struct __attribute__((packed)) {
    uint32_t first_sample_index;
    uint16_t sample_count;
    uint16_t dropped_samples;
    imu_wire_sample_t samples[IMU_SAMPLES_PER_PACKET];
} imu_wire_packet_t;

typedef struct __attribute__((packed)) {
    uint8_t ads_ok;
    uint8_t mpu_ok;
    uint8_t leads;
    uint8_t detector_state;
    uint8_t stream_enabled;
    uint8_t recording;
    uint8_t emg_calibrated;
    uint8_t imu_calibrating;
    uint8_t motion;
    uint32_t ads_samples;
    uint32_t ads_errors;
    uint32_t mpu_samples;
    uint32_t mpu_errors;
    uint32_t tx_drops;
    float fixed_on;
    float fixed_off;
    float adaptive_on;
    float adaptive_off;
    float on_coefficient;
    float off_coefficient;
    float gyro_bias_x;
    float gyro_bias_y;
    float gyro_bias_z;
    float motion_gyro_dps;
    float motion_accel_delta_g;
} status_wire_t;

typedef struct __attribute__((packed)) {
    uint8_t detector;
    uint8_t event;
    uint8_t state;
    uint8_t reserved;
    float envelope;
} event_wire_t;

typedef struct {
    bool ads1115_found;
    bool mpu6050_found;
    unsigned int found_count;
    unsigned int error_count;
} scan_result_t;

typedef struct {
    int16_t ax;
    int16_t ay;
    int16_t az;
    int16_t gx;
    int16_t gy;
    int16_t gz;
} mpu_raw_sample_t;

typedef struct {
    detector_state_t state;
    uint64_t candidate_since_us;
    uint64_t release_since_us;
    uint64_t refractory_until_us;
} detector_t;

static const char *TAG = "emcs";

static i2c_master_dev_handle_t g_ads1115;
static i2c_master_dev_handle_t g_mpu6050;
static QueueHandle_t g_tx_queue;
static TaskHandle_t g_ads_task_handle;

static volatile bool g_ads_ok = true;
static volatile bool g_mpu_ok = true;
static volatile bool g_stream_enabled;
static volatile bool g_recording;
static volatile bool g_emg_calibrated;
static volatile bool g_emg_calibration_requested = true;
static volatile bool g_imu_calibration_requested;
static volatile bool g_imu_calibrating;
static volatile bool g_motion;
static volatile uint8_t g_leads;
static volatile detector_state_t g_detector_state = DETECTOR_CALIBRATING;
static volatile uint32_t g_ads_samples;
static volatile uint32_t g_ads_errors;
static volatile uint32_t g_mpu_samples;
static volatile uint32_t g_mpu_errors;
static volatile uint32_t g_tx_drops;

static float g_fixed_on;
static float g_fixed_off;
static float g_adaptive_on;
static float g_adaptive_off;
static float g_on_coefficient = 6.0f;
static float g_off_coefficient = 3.0f;
static float g_gyro_bias[3];
static float g_motion_gyro_dps = 20.0f;
static float g_motion_accel_delta_g = 0.25f;

static float g_calibration_values[CALIBRATION_SAMPLES];
static float g_calibration_scratch[CALIBRATION_SAMPLES];
static uint16_t g_adaptive_histogram[ADAPTIVE_HIST_BINS];
static uint16_t g_adaptive_ring[ADAPTIVE_WINDOW_SAMPLES];
static size_t g_adaptive_ring_count;
static size_t g_adaptive_ring_position;

static uint16_t crc16_ccitt(const uint8_t *data, size_t length)
{
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < length; ++i) {
        crc ^= (uint16_t)data[i] << 8;
        for (int bit = 0; bit < 8; ++bit) {
            crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021)
                                 : (uint16_t)(crc << 1);
        }
    }
    return crc;
}

static bool queue_frame(uint8_t type, uint64_t timestamp_us,
                        const void *payload, size_t payload_length)
{
    if (payload_length > FRAME_MAX_PAYLOAD || g_tx_queue == NULL) {
        ++g_tx_drops;
        return false;
    }

    tx_packet_t packet = {
        .type = type,
        .payload_length = (uint16_t)payload_length,
        .timestamp_us = timestamp_us,
    };
    if (payload_length > 0 && payload != NULL) {
        memcpy(packet.payload, payload, payload_length);
    }
    if (xQueueSend(g_tx_queue, &packet, 0) != pdTRUE) {
        ++g_tx_drops;
        return false;
    }
    return true;
}

static void queue_text_frame(uint8_t type, const char *text)
{
    queue_frame(type, (uint64_t)esp_timer_get_time(), text, strlen(text));
}

static void queue_event(uint8_t detector, uint8_t event,
                        detector_state_t state, float envelope)
{
    if (!g_stream_enabled) {
        return;
    }
    const event_wire_t payload = {
        .detector = detector,
        .event = event,
        .state = (uint8_t)state,
        .envelope = envelope,
    };
    queue_frame(FRAME_EVENT, (uint64_t)esp_timer_get_time(),
                &payload, sizeof(payload));
}

static void tx_task(void *argument)
{
    (void)argument;
    uint32_t sequence = 0;
    tx_packet_t packet;
    uint8_t frame[sizeof(frame_header_t) + FRAME_MAX_PAYLOAD + sizeof(uint16_t)];

    while (true) {
        if (xQueueReceive(g_tx_queue, &packet, portMAX_DELAY) != pdTRUE) {
            continue;
        }

        const frame_header_t header = {
            .magic = FRAME_MAGIC,
            .version = FRAME_VERSION,
            .type = packet.type,
            .payload_length = packet.payload_length,
            .sequence = sequence++,
            .timestamp_us = packet.timestamp_us,
        };
        memcpy(frame, &header, sizeof(header));
        memcpy(frame + sizeof(header), packet.payload, packet.payload_length);
        const size_t crc_data_length = sizeof(header) - sizeof(header.magic)
                                     + packet.payload_length;
        uint16_t crc = crc16_ccitt(frame + sizeof(header.magic), crc_data_length);
        memcpy(frame + sizeof(header) + packet.payload_length, &crc, sizeof(crc));

        const size_t frame_length = sizeof(header) + packet.payload_length + sizeof(crc);
        const int written = uart_write_bytes(UART_NUM_0, frame, frame_length);
        if (written != (int)frame_length) {
            ++g_tx_drops;
        }
    }
}

static scan_result_t scan_i2c_bus(i2c_master_bus_handle_t bus)
{
    scan_result_t result = {0};

    ESP_LOGI(TAG, "Scanning I2C addresses 0x%02X-0x%02X at %u Hz",
             I2C_FIRST_ADDRESS, I2C_LAST_ADDRESS, I2C_FREQUENCY_HZ);
    for (uint16_t address = I2C_FIRST_ADDRESS; address <= I2C_LAST_ADDRESS; ++address) {
        esp_err_t err = i2c_master_probe(bus, address, I2C_PROBE_TIMEOUT_MS);
        if (err == ESP_OK) {
            ESP_LOGI(TAG, "Found I2C device at address 0x%02X", address);
            ++result.found_count;
            result.ads1115_found |= address == ADS1115_ADDRESS;
            result.mpu6050_found |= address == MPU6050_ADDRESS;
        } else if (err != ESP_ERR_NOT_FOUND) {
            ESP_LOGE(TAG, "I2C error at address 0x%02X: %s",
                     address, esp_err_to_name(err));
            ++result.error_count;
        }
    }
    ESP_LOGI(TAG, "Scan complete: found=%u, errors=%u, ADS1115(0x48)=%s, MPU6050(0x68)=%s",
             result.found_count, result.error_count,
             result.ads1115_found ? "FOUND" : "NOT FOUND",
             result.mpu6050_found ? "FOUND" : "NOT FOUND");
    return result;
}

static esp_err_t add_i2c_device(i2c_master_bus_handle_t bus, uint16_t address,
                                i2c_master_dev_handle_t *device)
{
    const i2c_device_config_t config = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = address,
        .scl_speed_hz = I2C_FREQUENCY_HZ,
    };
    return i2c_master_bus_add_device(bus, &config, device);
}

static esp_err_t write_register8(i2c_master_dev_handle_t device,
                                 uint8_t reg, uint8_t value)
{
    const uint8_t data[] = {reg, value};
    return i2c_master_transmit(device, data, sizeof(data),
                               I2C_TRANSFER_TIMEOUT_MS);
}

static esp_err_t read_registers(i2c_master_dev_handle_t device, uint8_t reg,
                                uint8_t *data, size_t data_size)
{
    return i2c_master_transmit_receive(device, &reg, 1, data, data_size,
                                       I2C_TRANSFER_TIMEOUT_MS);
}

static int16_t decode_be_i16(const uint8_t *data)
{
    return (int16_t)(((uint16_t)data[0] << 8) | data[1]);
}

static esp_err_t configure_ads1115(void)
{
    const uint8_t config[] = {
        ADS1115_REG_CONFIG,
        (uint8_t)(ADS1115_CONFIG_EMG >> 8),
        (uint8_t)ADS1115_CONFIG_EMG,
    };
    esp_err_t err = i2c_master_transmit(g_ads1115, config, sizeof(config),
                                        I2C_TRANSFER_TIMEOUT_MS);
    if (err != ESP_OK) {
        return err;
    }
    vTaskDelay(pdMS_TO_TICKS(10));

    uint8_t readback[2];
    err = read_registers(g_ads1115, ADS1115_REG_CONFIG, readback, sizeof(readback));
    if (err != ESP_OK) {
        return err;
    }
    const uint16_t actual = ((uint16_t)readback[0] << 8) | readback[1];
    if ((actual & 0x7FFF) != ADS1115_CONFIG_EMG) {
        ESP_LOGE(TAG, "ADS1115 config mismatch: expected 0x%04X, got 0x%04X",
                 ADS1115_CONFIG_EMG, actual);
        return ESP_ERR_INVALID_RESPONSE;
    }
    ESP_LOGI(TAG, "ADS1115: AIN0-GND, continuous 860 SPS, +/-4.096 V, config=0x%04X",
             actual);
    return ESP_OK;
}

static esp_err_t configure_mpu6050(void)
{
    uint8_t who_am_i;
    esp_err_t err = read_registers(g_mpu6050, MPU6050_REG_WHO_AM_I, &who_am_i, 1);
    if (err != ESP_OK) {
        return err;
    }
    if (who_am_i != MPU6050_WHO_AM_I_VALUE) {
        ESP_LOGE(TAG, "MPU6050 WHO_AM_I mismatch: expected 0x68, got 0x%02X", who_am_i);
        return ESP_ERR_INVALID_RESPONSE;
    }
    ESP_LOGI(TAG, "MPU6050 WHO_AM_I=0x%02X: OK", who_am_i);

    const struct {
        uint8_t reg;
        uint8_t value;
    } settings[] = {
        {MPU6050_REG_PWR_MGMT_1, 0x01},
        {MPU6050_REG_CONFIG, 0x03},
        {MPU6050_REG_SMPLRT_DIV, 9},
        {MPU6050_REG_ACCEL_CONFIG, 0x00},
        {MPU6050_REG_GYRO_CONFIG, 0x00},
    };
    for (size_t i = 0; i < sizeof(settings) / sizeof(settings[0]); ++i) {
        err = write_register8(g_mpu6050, settings[i].reg, settings[i].value);
        if (err != ESP_OK) {
            return err;
        }
    }
    vTaskDelay(pdMS_TO_TICKS(100));

    uint8_t readback[4];
    err = read_registers(g_mpu6050, MPU6050_REG_SMPLRT_DIV, readback, sizeof(readback));
    if (err != ESP_OK) {
        return err;
    }
    if (readback[0] != 9 || (readback[1] & 0x07) != 3 ||
        (readback[2] & 0x18) != 0 || (readback[3] & 0x18) != 0) {
        ESP_LOGE(TAG, "MPU6050 config verification failed: div=%u dlpf=%u gyro=0x%02X accel=0x%02X",
                 readback[0], readback[1], readback[2], readback[3]);
        return ESP_ERR_INVALID_RESPONSE;
    }
    ESP_LOGI(TAG, "MPU6050: 100 Hz, +/-2 g, +/-250 dps");
    return ESP_OK;
}

static esp_err_t read_mpu6050_raw(mpu_raw_sample_t *sample)
{
    uint8_t data[14];
    esp_err_t err = read_registers(g_mpu6050, MPU6050_REG_ACCEL_XOUT_H,
                                   data, sizeof(data));
    if (err != ESP_OK) {
        return err;
    }
    sample->ax = decode_be_i16(&data[0]);
    sample->ay = decode_be_i16(&data[2]);
    sample->az = decode_be_i16(&data[4]);
    sample->gx = decode_be_i16(&data[8]);
    sample->gy = decode_be_i16(&data[10]);
    sample->gz = decode_be_i16(&data[12]);
    return ESP_OK;
}

static esp_err_t calibrate_imu_blocking(void)
{
    int64_t sums[3] = {0};
    int collected = 0;
    g_imu_calibrating = true;

    while (collected < MPU_GYRO_CAL_SAMPLES) {
        mpu_raw_sample_t sample;
        esp_err_t err = read_mpu6050_raw(&sample);
        if (err != ESP_OK) {
            g_imu_calibrating = false;
            return err;
        }
        sums[0] += sample.gx;
        sums[1] += sample.gy;
        sums[2] += sample.gz;
        ++collected;
        vTaskDelay(pdMS_TO_TICKS(MPU_SAMPLE_PERIOD_MS));
    }
    for (int axis = 0; axis < 3; ++axis) {
        g_gyro_bias[axis] = (float)sums[axis] / MPU_GYRO_CAL_SAMPLES;
    }
    g_imu_calibrating = false;
    return ESP_OK;
}

static int compare_float(const void *left, const void *right)
{
    const float a = *(const float *)left;
    const float b = *(const float *)right;
    return (a > b) - (a < b);
}

static float median_sorted(float *values, size_t count)
{
    qsort(values, count, sizeof(float), compare_float);
    if ((count & 1U) != 0) {
        return values[count / 2];
    }
    return 0.5f * (values[count / 2 - 1] + values[count / 2]);
}

static void robust_statistics(const float *values, size_t count,
                              float *median, float *sigma)
{
    memcpy(g_calibration_scratch, values, count * sizeof(float));
    *median = median_sorted(g_calibration_scratch, count);
    for (size_t i = 0; i < count; ++i) {
        g_calibration_scratch[i] = fabsf(values[i] - *median);
    }
    const float mad = median_sorted(g_calibration_scratch, count);
    *sigma = fmaxf(1.4826f * mad, 0.5f);
}

static uint16_t envelope_to_bin(float envelope)
{
    int bin = (int)(envelope / ADAPTIVE_BIN_WIDTH + 0.5f);
    if (bin < 0) {
        return 0;
    }
    if (bin >= ADAPTIVE_HIST_BINS) {
        return ADAPTIVE_HIST_BINS - 1;
    }
    return (uint16_t)bin;
}

static void adaptive_baseline_add(float envelope)
{
    const uint16_t bin = envelope_to_bin(envelope);
    if (g_adaptive_ring_count == ADAPTIVE_WINDOW_SAMPLES) {
        const uint16_t old = g_adaptive_ring[g_adaptive_ring_position];
        if (g_adaptive_histogram[old] > 0) {
            --g_adaptive_histogram[old];
        }
    } else {
        ++g_adaptive_ring_count;
    }
    g_adaptive_ring[g_adaptive_ring_position] = bin;
    ++g_adaptive_histogram[bin];
    g_adaptive_ring_position =
        (g_adaptive_ring_position + 1) % ADAPTIVE_WINDOW_SAMPLES;
}

static void adaptive_baseline_reset(const float *values, size_t count)
{
    memset(g_adaptive_histogram, 0, sizeof(g_adaptive_histogram));
    g_adaptive_ring_count = 0;
    g_adaptive_ring_position = 0;
    for (size_t i = 0; i < count; ++i) {
        adaptive_baseline_add(values[i]);
    }
}

static void adaptive_robust_statistics(float *median, float *sigma)
{
    if (g_adaptive_ring_count == 0) {
        *median = 0.0f;
        *sigma = 0.5f;
        return;
    }
    const uint32_t target = (uint32_t)(g_adaptive_ring_count + 1) / 2;
    uint32_t accumulated = 0;
    int median_bin = 0;
    for (int bin = 0; bin < ADAPTIVE_HIST_BINS; ++bin) {
        accumulated += g_adaptive_histogram[bin];
        if (accumulated >= target) {
            median_bin = bin;
            break;
        }
    }

    accumulated = g_adaptive_histogram[median_bin];
    int deviation_bins = 0;
    while (accumulated < target && deviation_bins < ADAPTIVE_HIST_BINS - 1) {
        ++deviation_bins;
        if (median_bin - deviation_bins >= 0) {
            accumulated += g_adaptive_histogram[median_bin - deviation_bins];
        }
        if (median_bin + deviation_bins < ADAPTIVE_HIST_BINS) {
            accumulated += g_adaptive_histogram[median_bin + deviation_bins];
        }
    }
    *median = median_bin * ADAPTIVE_BIN_WIDTH;
    *sigma = fmaxf(1.4826f * deviation_bins * ADAPTIVE_BIN_WIDTH, 0.5f);
}

static uint8_t update_detector(detector_t *detector, float envelope,
                               float threshold_on, float threshold_off,
                               bool leads_ok, bool calibrating, uint64_t now_us)
{
    uint8_t events = 0;
    if (!leads_ok) {
        detector->state = DETECTOR_LEADS_OFF;
        detector->candidate_since_us = 0;
        detector->release_since_us = 0;
        return events;
    }
    if (calibrating || !g_emg_calibrated) {
        detector->state = DETECTOR_CALIBRATING;
        detector->candidate_since_us = 0;
        detector->release_since_us = 0;
        return events;
    }

    switch (detector->state) {
    case DETECTOR_LEADS_OFF:
    case DETECTOR_CALIBRATING:
        detector->state = DETECTOR_REST;
        break;
    case DETECTOR_REST:
        if (envelope >= threshold_on) {
            detector->state = DETECTOR_CANDIDATE;
            detector->candidate_since_us = now_us;
        }
        break;
    case DETECTOR_CANDIDATE:
        if (envelope < threshold_on) {
            detector->state = DETECTOR_REST;
            detector->candidate_since_us = 0;
        } else if (now_us - detector->candidate_since_us >= CONTRACTION_CONFIRM_US) {
            detector->state = DETECTOR_ACTIVE;
            detector->release_since_us = 0;
            events |= 0x01;
        }
        break;
    case DETECTOR_ACTIVE:
        if (envelope <= threshold_off) {
            if (detector->release_since_us == 0) {
                detector->release_since_us = now_us;
            } else if (now_us - detector->release_since_us >= RELEASE_CONFIRM_US) {
                detector->state = DETECTOR_REFRACTORY;
                detector->refractory_until_us = now_us + REFRACTORY_US;
                detector->release_since_us = 0;
                events |= 0x02;
            }
        } else {
            detector->release_since_us = 0;
        }
        break;
    case DETECTOR_REFRACTORY:
        if (now_us >= detector->refractory_until_us) {
            detector->state = DETECTOR_REST;
        }
        break;
    default:
        detector->state = DETECTOR_REST;
        break;
    }
    return events;
}

static void send_status(void)
{
    const status_wire_t status = {
        .ads_ok = g_ads_ok,
        .mpu_ok = g_mpu_ok,
        .leads = g_leads,
        .detector_state = (uint8_t)g_detector_state,
        .stream_enabled = g_stream_enabled,
        .recording = g_recording,
        .emg_calibrated = g_emg_calibrated,
        .imu_calibrating = g_imu_calibrating,
        .motion = g_motion,
        .ads_samples = g_ads_samples,
        .ads_errors = g_ads_errors,
        .mpu_samples = g_mpu_samples,
        .mpu_errors = g_mpu_errors,
        .tx_drops = g_tx_drops,
        .fixed_on = g_fixed_on,
        .fixed_off = g_fixed_off,
        .adaptive_on = g_adaptive_on,
        .adaptive_off = g_adaptive_off,
        .on_coefficient = g_on_coefficient,
        .off_coefficient = g_off_coefficient,
        .gyro_bias_x = g_gyro_bias[0],
        .gyro_bias_y = g_gyro_bias[1],
        .gyro_bias_z = g_gyro_bias[2],
        .motion_gyro_dps = g_motion_gyro_dps,
        .motion_accel_delta_g = g_motion_accel_delta_g,
    };
    queue_frame(FRAME_STATUS, (uint64_t)esp_timer_get_time(),
                &status, sizeof(status));
}

static void handle_command(const char *line)
{
    if (strcmp(line, "STATUS") == 0) {
        send_status();
    } else if (strcmp(line, "PING") == 0) {
        queue_text_frame(FRAME_ACK, "PONG");
    } else if (strcmp(line, "STREAM START") == 0) {
        queue_text_frame(FRAME_ACK, "STREAM START");
        g_stream_enabled = true;
    } else if (strcmp(line, "STREAM STOP") == 0) {
        g_stream_enabled = false;
        queue_text_frame(FRAME_ACK, "STREAM STOP");
    } else if (strcmp(line, "RECORD START") == 0) {
        g_recording = true;
        queue_text_frame(FRAME_ACK, "RECORD START");
    } else if (strcmp(line, "RECORD STOP") == 0) {
        g_recording = false;
        queue_text_frame(FRAME_ACK, "RECORD STOP");
    } else if (strcmp(line, "CAL EMG") == 0) {
        g_emg_calibration_requested = true;
        queue_text_frame(FRAME_ACK, "CAL EMG");
    } else if (strcmp(line, "CAL IMU") == 0) {
        g_imu_calibration_requested = true;
        queue_text_frame(FRAME_ACK, "CAL IMU");
    } else {
        float first;
        float second;
        if (sscanf(line, "SET THRESH %f %f", &first, &second) == 2) {
            if (first > second && second > 0.0f && first <= 20.0f) {
                g_on_coefficient = first;
                g_off_coefficient = second;
                queue_text_frame(FRAME_ACK, "SET THRESH");
            } else {
                queue_text_frame(FRAME_ERROR, "Invalid threshold coefficients");
            }
        } else if (sscanf(line, "SET IMU %f %f", &first, &second) == 2) {
            if (first > 0.0f && second > 0.0f) {
                g_motion_gyro_dps = first;
                g_motion_accel_delta_g = second;
                queue_text_frame(FRAME_ACK, "SET IMU");
            } else {
                queue_text_frame(FRAME_ERROR, "Invalid IMU thresholds");
            }
        } else {
            queue_text_frame(FRAME_ERROR, "Unknown command");
        }
    }
}

static void command_task(void *argument)
{
    (void)argument;
    char line[128];
    size_t length = 0;

    while (true) {
        uint8_t byte;
        const int received = uart_read_bytes(UART_NUM_0, &byte, 1, portMAX_DELAY);
        if (received <= 0) {
            continue;
        }
        if (byte == '\r' || byte == '\n') {
            if (length > 0) {
                line[length] = '\0';
                handle_command(line);
                length = 0;
            }
        } else if (length < sizeof(line) - 1) {
            line[length++] = (char)byte;
        } else {
            length = 0;
            queue_text_frame(FRAME_ERROR, "Command too long");
        }
    }
}

static void ads_timer_callback(void *argument)
{
    (void)argument;
    if (g_ads_task_handle != NULL) {
        xTaskNotifyGive(g_ads_task_handle);
    }
}

static void ads_task(void *argument)
{
    (void)argument;
    detector_t fixed_detector = {.state = DETECTOR_CALIBRATING};
    detector_t adaptive_detector = {.state = DETECTOR_CALIBRATING};
    float dc_baseline = 0.0f;
    bool dc_initialized = false;
    float rms_ring[RMS_WINDOW_SAMPLES] = {0};
    size_t rms_position = 0;
    size_t rms_count = 0;
    float rms_sum = 0.0f;
    size_t calibration_count = 0;
    bool calibrating = false;
    uint32_t adaptation_samples = 0;
    uint8_t previous_leads = 0xFF;
    bool ads_error_reported = false;
    uint32_t sample_index = 0;
    uint16_t dropped_since_packet = 0;
    emg_wire_packet_t wire_packet = {0};

    while (true) {
        uint32_t notification_count = ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        if (notification_count == 0) {
            continue;
        }
        uint8_t timer_gap = notification_count > UINT8_MAX
                          ? UINT8_MAX : (uint8_t)notification_count;
        if (notification_count > 1) {
            uint32_t dropped = notification_count - 1;
            dropped_since_packet = (uint16_t)fminf(UINT16_MAX,
                                                   dropped_since_packet + dropped);
            sample_index += dropped;
        }

        const uint64_t now_us = (uint64_t)esp_timer_get_time();
        uint8_t bytes[2];
        esp_err_t err = read_registers(g_ads1115, ADS1115_REG_CONVERSION,
                                       bytes, sizeof(bytes));
        if (err != ESP_OK) {
            ++g_ads_errors;
            g_ads_ok = false;
            g_detector_state = DETECTOR_LEADS_OFF;
            if (!ads_error_reported) {
                char message[96];
                snprintf(message, sizeof(message), "ADS1115 stopped responding: %s",
                         esp_err_to_name(err));
                queue_text_frame(FRAME_ERROR, message);
                ads_error_reported = true;
            }
            continue;
        }
        if (ads_error_reported) {
            queue_text_frame(FRAME_ACK, "ADS1115 responding again");
            ads_error_reported = false;
        }
        g_ads_ok = true;
        ++g_ads_samples;
        ++sample_index;

        const int16_t raw = decode_be_i16(bytes);
        const uint8_t leads =
            (gpio_get_level(AD8232_LO_MINUS_GPIO) ? 0x01 : 0x00) |
            (gpio_get_level(AD8232_LO_PLUS_GPIO) ? 0x02 : 0x00);
        g_leads = leads;
        const bool leads_ok = leads == 0;
        if (leads != previous_leads) {
            queue_event(2, EVENT_LEADS_CHANGED,
                        leads_ok ? DETECTOR_CALIBRATING : DETECTOR_LEADS_OFF,
                        0.0f);
            previous_leads = leads;
        }

        float envelope = 0.0f;
        if (!leads_ok) {
            calibrating = false;
            calibration_count = 0;
            dc_initialized = false;
            rms_position = 0;
            rms_count = 0;
            rms_sum = 0.0f;
            memset(rms_ring, 0, sizeof(rms_ring));
        } else {
            if (!dc_initialized) {
                dc_baseline = raw;
                dc_initialized = true;
            }
            dc_baseline += DC_TRACKING_ALPHA * ((float)raw - dc_baseline);
            const float centered = (float)raw - dc_baseline;
            const float squared = centered * centered;
            rms_sum -= rms_ring[rms_position];
            rms_ring[rms_position] = squared;
            rms_sum += squared;
            rms_position = (rms_position + 1) % RMS_WINDOW_SAMPLES;
            if (rms_count < RMS_WINDOW_SAMPLES) {
                ++rms_count;
            }
            envelope = sqrtf(fmaxf(rms_sum / rms_count, 0.0f));

            if (g_emg_calibration_requested && !calibrating) {
                calibrating = true;
                g_emg_calibrated = false;
                g_emg_calibration_requested = false;
                calibration_count = 0;
            }
            if (calibrating) {
                if (g_motion) {
                    calibration_count = 0;
                } else if (rms_count == RMS_WINDOW_SAMPLES) {
                    g_calibration_values[calibration_count++] = envelope;
                }
                if (calibration_count == CALIBRATION_SAMPLES) {
                    float median;
                    float sigma;
                    robust_statistics(g_calibration_values, calibration_count,
                                      &median, &sigma);
                    g_fixed_on = median + g_on_coefficient * sigma;
                    g_fixed_off = median + g_off_coefficient * sigma;
                    g_adaptive_on = g_fixed_on;
                    g_adaptive_off = g_fixed_off;
                    adaptive_baseline_reset(g_calibration_values, calibration_count);
                    g_emg_calibrated = true;
                    calibrating = false;
                    fixed_detector.state = DETECTOR_REST;
                    adaptive_detector.state = DETECTOR_REST;
                    queue_event(2, EVENT_EMG_CALIBRATION_DONE,
                                DETECTOR_REST, envelope);
                }
            }
        }

        const uint8_t fixed_events = update_detector(
            &fixed_detector, envelope, g_fixed_on, g_fixed_off,
            leads_ok, calibrating, now_us);
        const uint8_t adaptive_events = update_detector(
            &adaptive_detector, envelope, g_adaptive_on, g_adaptive_off,
            leads_ok, calibrating, now_us);
        g_detector_state = adaptive_detector.state;

        if (fixed_events & 0x01) {
            queue_event(0, EVENT_CONTRACTION_START,
                        fixed_detector.state, envelope);
        }
        if (fixed_events & 0x02) {
            queue_event(0, EVENT_CONTRACTION_RELEASE,
                        fixed_detector.state, envelope);
        }
        if (adaptive_events & 0x01) {
            queue_event(1, EVENT_CONTRACTION_START,
                        adaptive_detector.state, envelope);
        }
        if (adaptive_events & 0x02) {
            queue_event(1, EVENT_CONTRACTION_RELEASE,
                        adaptive_detector.state, envelope);
        }

        if (g_emg_calibrated && leads_ok && !g_motion &&
            adaptive_detector.state == DETECTOR_REST) {
            adaptive_baseline_add(envelope);
            if (++adaptation_samples >= ADAPTIVE_UPDATE_SAMPLES) {
                float median;
                float sigma;
                adaptive_robust_statistics(&median, &sigma);
                const float target_on = median + g_on_coefficient * sigma;
                const float target_off = median + g_off_coefficient * sigma;
                g_adaptive_on += 0.1f * (target_on - g_adaptive_on);
                g_adaptive_off += 0.1f * (target_off - g_adaptive_off);
                adaptation_samples = 0;
            }
        }

        if (!g_stream_enabled) {
            wire_packet.sample_count = 0;
            dropped_since_packet = 0;
            continue;
        }
        if (wire_packet.sample_count == 0) {
            wire_packet.first_sample_index = sample_index;
            wire_packet.dropped_samples = 0;
            wire_packet.samples[0].timer_gap = 1;
        }
        const size_t index = wire_packet.sample_count;
        wire_packet.samples[index].raw = raw;
        wire_packet.samples[index].envelope = envelope;
        wire_packet.samples[index].flags =
            (fixed_detector.state == DETECTOR_ACTIVE ? 0x01 : 0) |
            (adaptive_detector.state == DETECTOR_ACTIVE ? 0x02 : 0) |
            (fixed_events & 0x01 ? 0x04 : 0) |
            (adaptive_events & 0x01 ? 0x08 : 0) |
            (fixed_events & 0x02 ? 0x10 : 0) |
            (adaptive_events & 0x02 ? 0x20 : 0);
        wire_packet.samples[index].timer_gap =
            index == 0 ? 1 : timer_gap;
        ++wire_packet.sample_count;

        if (wire_packet.sample_count == EMG_SAMPLES_PER_PACKET) {
            wire_packet.dropped_samples = dropped_since_packet;
            wire_packet.adaptive_on = g_adaptive_on;
            wire_packet.adaptive_off = g_adaptive_off;
            wire_packet.fixed_on = g_fixed_on;
            wire_packet.fixed_off = g_fixed_off;
            wire_packet.detector_state = (uint8_t)adaptive_detector.state;
            wire_packet.leads = leads;
            wire_packet.motion = g_motion;
            wire_packet.recording = g_recording;
            const size_t payload_size = offsetof(emg_wire_packet_t, samples)
                                      + wire_packet.sample_count
                                      * sizeof(emg_wire_sample_t);
            const uint64_t first_timestamp =
                now_us - (uint64_t)(wire_packet.sample_count - 1)
                       * ADS_SAMPLE_PERIOD_US;
            queue_frame(FRAME_EMG_BATCH, first_timestamp,
                        &wire_packet, payload_size);
            wire_packet.sample_count = 0;
            dropped_since_packet = 0;
        }
    }
}

static void imu_task(void *argument)
{
    (void)argument;
    TickType_t last_wake = xTaskGetTickCount();
    bool mpu_error_reported = false;
    uint32_t sample_index = 0;
    imu_wire_packet_t wire_packet = {0};

    while (true) {
        vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(MPU_SAMPLE_PERIOD_MS));
        if (g_imu_calibration_requested) {
            g_imu_calibration_requested = false;
            esp_err_t calibration_err = calibrate_imu_blocking();
            last_wake = xTaskGetTickCount();
            if (calibration_err == ESP_OK) {
                queue_event(2, EVENT_IMU_CALIBRATION_DONE,
                            g_detector_state, 0.0f);
            } else {
                queue_text_frame(FRAME_ERROR, "MPU6050 calibration failed");
            }
            wire_packet.sample_count = 0;
            continue;
        }

        const uint64_t now_us = (uint64_t)esp_timer_get_time();
        mpu_raw_sample_t raw;
        esp_err_t err = read_mpu6050_raw(&raw);
        if (err != ESP_OK) {
            ++g_mpu_errors;
            g_mpu_ok = false;
            g_motion = true;
            if (!mpu_error_reported) {
                char message[96];
                snprintf(message, sizeof(message), "MPU6050 stopped responding: %s",
                         esp_err_to_name(err));
                queue_text_frame(FRAME_ERROR, message);
                mpu_error_reported = true;
            }
            continue;
        }
        if (mpu_error_reported) {
            queue_text_frame(FRAME_ACK, "MPU6050 responding again");
            mpu_error_reported = false;
        }
        g_mpu_ok = true;
        ++g_mpu_samples;
        ++sample_index;

        const int16_t gx = (int16_t)lroundf(raw.gx - g_gyro_bias[0]);
        const int16_t gy = (int16_t)lroundf(raw.gy - g_gyro_bias[1]);
        const int16_t gz = (int16_t)lroundf(raw.gz - g_gyro_bias[2]);
        const float gx_dps = gx / MPU6050_GYRO_LSB_PER_DPS;
        const float gy_dps = gy / MPU6050_GYRO_LSB_PER_DPS;
        const float gz_dps = gz / MPU6050_GYRO_LSB_PER_DPS;
        const float gyro_magnitude =
            sqrtf(gx_dps * gx_dps + gy_dps * gy_dps + gz_dps * gz_dps);
        const float ax_g = raw.ax / MPU6050_ACCEL_LSB_PER_G;
        const float ay_g = raw.ay / MPU6050_ACCEL_LSB_PER_G;
        const float az_g = raw.az / MPU6050_ACCEL_LSB_PER_G;
        const float accel_magnitude =
            sqrtf(ax_g * ax_g + ay_g * ay_g + az_g * az_g);
        g_motion = gyro_magnitude > g_motion_gyro_dps ||
                   fabsf(accel_magnitude - 1.0f) > g_motion_accel_delta_g;

        if (!g_stream_enabled) {
            wire_packet.sample_count = 0;
            continue;
        }
        if (wire_packet.sample_count == 0) {
            wire_packet.first_sample_index = sample_index;
            wire_packet.dropped_samples = 0;
        }
        const size_t index = wire_packet.sample_count;
        wire_packet.samples[index] = (imu_wire_sample_t) {
            .ax = raw.ax,
            .ay = raw.ay,
            .az = raw.az,
            .gx = gx,
            .gy = gy,
            .gz = gz,
            .timer_gap = 1,
        };
        ++wire_packet.sample_count;

        if (wire_packet.sample_count == IMU_SAMPLES_PER_PACKET) {
            const size_t payload_size = offsetof(imu_wire_packet_t, samples)
                                      + wire_packet.sample_count
                                      * sizeof(imu_wire_sample_t);
            const uint64_t first_timestamp =
                now_us - (uint64_t)(wire_packet.sample_count - 1)
                       * MPU_SAMPLE_PERIOD_MS * 1000ULL;
            queue_frame(FRAME_IMU_BATCH, first_timestamp,
                        &wire_packet, payload_size);
            wire_packet.sample_count = 0;
        }
    }
}

void app_main(void)
{
    setvbuf(stdout, NULL, _IONBF, 0);

    esp_err_t err = uart_driver_install(UART_NUM_0, 512, 0, 0, NULL, 0);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "UART command receiver initialization failed: %s",
                 esp_err_to_name(err));
        return;
    }
    err = uart_set_baudrate(UART_NUM_0, SERIAL_BAUD_RATE);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "UART baud-rate configuration failed: %s",
                 esp_err_to_name(err));
        return;
    }

    const gpio_config_t lead_gpio_config = {
        .pin_bit_mask = (1ULL << AD8232_LO_MINUS_GPIO)
                      | (1ULL << AD8232_LO_PLUS_GPIO),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    err = gpio_config(&lead_gpio_config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Lead-off GPIO configuration failed: %s", esp_err_to_name(err));
        return;
    }

    const i2c_master_bus_config_t bus_config = {
        .i2c_port = I2C_NUM_0,
        .sda_io_num = I2C_SDA_GPIO,
        .scl_io_num = I2C_SCL_GPIO,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    i2c_master_bus_handle_t bus;
    ESP_LOGI(TAG, "I2C: SDA=GPIO%d, SCL=GPIO%d, frequency=%u Hz",
             I2C_SDA_GPIO, I2C_SCL_GPIO, I2C_FREQUENCY_HZ);
    err = i2c_new_master_bus(&bus_config, &bus);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "I2C initialization failed: %s; stopped without reboot",
                 esp_err_to_name(err));
        return;
    }

    const scan_result_t scan = scan_i2c_bus(bus);
    if (!scan.ads1115_found || !scan.mpu6050_found) {
        ESP_LOGE(TAG, "Required I2C devices missing; stopped without reboot");
        return;
    }
    if ((err = add_i2c_device(bus, ADS1115_ADDRESS, &g_ads1115)) != ESP_OK ||
        (err = add_i2c_device(bus, MPU6050_ADDRESS, &g_mpu6050)) != ESP_OK) {
        ESP_LOGE(TAG, "I2C device registration failed: %s", esp_err_to_name(err));
        return;
    }
    if ((err = configure_ads1115()) != ESP_OK) {
        ESP_LOGE(TAG, "ADS1115 configuration failed: %s", esp_err_to_name(err));
        return;
    }
    if ((err = configure_mpu6050()) != ESP_OK) {
        ESP_LOGE(TAG, "MPU6050 configuration failed: %s", esp_err_to_name(err));
        return;
    }

    ESP_LOGI(TAG, "Keep the MPU6050 stationary: calibrating gyro for %.1f s",
             MPU_GYRO_CAL_SAMPLES * MPU_SAMPLE_PERIOD_MS / 1000.0f);
    if ((err = calibrate_imu_blocking()) != ESP_OK) {
        ESP_LOGE(TAG, "Initial MPU6050 calibration failed: %s", esp_err_to_name(err));
        return;
    }
    ESP_LOGI(TAG, "Gyro bias raw: x=%.2f y=%.2f z=%.2f",
             g_gyro_bias[0], g_gyro_bias[1], g_gyro_bias[2]);

    g_tx_queue = xQueueCreate(TX_QUEUE_DEPTH, sizeof(tx_packet_t));
    if (g_tx_queue == NULL) {
        ESP_LOGE(TAG, "Cannot allocate transmit queue");
        return;
    }
    xTaskCreate(tx_task, "emcs_tx", 4096, NULL, 8, NULL);
    xTaskCreate(command_task, "emcs_command", 4096, NULL, 7, NULL);
    xTaskCreate(imu_task, "emcs_imu", 4096, NULL, 10, NULL);
    xTaskCreate(ads_task, "emcs_ads", 8192, NULL, 12, &g_ads_task_handle);

    const esp_timer_create_args_t timer_args = {
        .callback = ads_timer_callback,
        .name = "ads860",
    };
    esp_timer_handle_t ads_timer;
    if ((err = esp_timer_create(&timer_args, &ads_timer)) != ESP_OK ||
        (err = esp_timer_start_periodic(ads_timer, ADS_SAMPLE_PERIOD_US)) != ESP_OK) {
        ESP_LOGE(TAG, "ADS sampling timer failed: %s", esp_err_to_name(err));
        return;
    }

    ESP_LOGI(TAG, "Ready. Binary stream is stopped; connect the desktop app or send STATUS.");
    ESP_LOGI(TAG, "Commands: STATUS, STREAM START|STOP, RECORD START|STOP, CAL EMG, CAL IMU, SET THRESH on off, SET IMU gyro accel");
}
