#include <Arduino.h>
#include "can.h"
#include "can_hw.h"
#include "nvic.h"

static volatile bool g_rx_pending = false;
static CanFrame g_rx_frame;

static void on_rx(const CanFrame *frame)
{
    g_rx_frame = *frame;
    g_rx_pending = true;
}

void can_init(uint32_t bitrate)
{
    can_hw_set_bitrate(bitrate);
    can_hw_enable_tx();
    can_hw_register_rx_callback(on_rx);
    nvic_enable(CAN_RX_IRQ_NUMBER);
}

bool can_send(const CanFrame *frame)
{
    return can_hw_write(frame);
}

bool can_receive(CanFrame *frame, uint32_t timeout_ms)
{
    uint32_t start = millis();
    while (!g_rx_pending) {
        if (millis() - start >= timeout_ms) {
            return false;
        }
    }
    noInterrupts();
    *frame = g_rx_frame;
    g_rx_pending = false;
    interrupts();
    return true;
}
