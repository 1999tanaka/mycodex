#include <Arduino.h>
#include "sensor.h"

static const uint8_t SENSOR_PIN = A0;

void sensor_init(void)
{
    pinMode(SENSOR_PIN, INPUT);
}

uint16_t sensor_read_raw(void)
{
    return (uint16_t)analogRead(SENSOR_PIN);
}
