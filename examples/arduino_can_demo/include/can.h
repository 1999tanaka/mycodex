#pragma once

#include <stdint.h>
#include <stdbool.h>

typedef struct {
    uint32_t id;
    uint8_t  dlc;
    uint8_t  data[8];
} CanFrame;

void can_init(uint32_t bitrate);
bool can_send(const CanFrame *frame);
bool can_receive(CanFrame *frame, uint32_t timeout_ms);
