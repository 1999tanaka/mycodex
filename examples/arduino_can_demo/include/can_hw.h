#pragma once

#include <stdint.h>
#include "can.h"

/* CAN controller low level driver (board specific) */
void can_hw_set_bitrate(uint32_t bitrate);
void can_hw_enable_tx(void);
bool can_hw_write(const CanFrame *frame);

/* RX FIFO0 message pending interrupt */
void can_hw_enable_rx_interrupt(void);
void can_hw_register_rx_callback(void (*cb)(const CanFrame *frame));

#define CAN_RX_IRQ_NUMBER 20
