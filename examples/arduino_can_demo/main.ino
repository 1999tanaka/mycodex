#include "include/can.h"
#include "include/sensor.h"

void setup()
{
    Serial.begin(115200);
    Serial.println("BOOT");
    sensor_init();
    can_init(500000);
}

void loop()
{
    Serial.print("TEST:UART:PASS\n");

    uint16_t adc = sensor_read_raw();
    if (adc > 100 && adc < 4000) {
        Serial.print("TEST:ADC:PASS:value=");
        Serial.println(adc);
    } else {
        Serial.println("TEST:ADC:FAIL:OUT_OF_RANGE");
    }

    CanFrame tx = {0x123, 2, {0xAB, 0xCD}};
    Serial.println(can_send(&tx) ? "TEST:CAN_TX:PASS" : "TEST:CAN_TX:FAIL:TX_ERROR");

    CanFrame rx;
    Serial.println(can_receive(&rx, 100) ? "TEST:CAN_RX:PASS" : "TEST:CAN_RX:FAIL:RX_TIMEOUT");

    Serial.println("TEST:END");
    delay(1000);
}
