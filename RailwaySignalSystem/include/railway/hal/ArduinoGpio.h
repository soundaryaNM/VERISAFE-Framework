#pragma once

#include "railway/hal/IGpio.h"

namespace railway::hal {

// Arduino/ESP32 style GPIO implementation.
// This is intentionally hardware-dependent and should be compiled only for embedded targets.
class ArduinoGpio final : public IGpio {
public:
    void configure(Pin pin, PinMode mode) override;
    PinLevel read(Pin pin) const override;
    void write(Pin pin, PinLevel level) override;
};

} // namespace railway::hal
