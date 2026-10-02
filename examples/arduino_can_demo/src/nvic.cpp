#include "nvic.h"

/* Nested vectored interrupt controller helpers */
static volatile uint32_t *const ISER = (volatile uint32_t *)0xE000E100UL;
static volatile uint32_t *const ICER = (volatile uint32_t *)0xE000E180UL;

void nvic_enable(uint8_t irq)
{
    ISER[irq >> 5] = 1UL << (irq & 0x1F);
}

void nvic_disable(uint8_t irq)
{
    ICER[irq >> 5] = 1UL << (irq & 0x1F);
}
