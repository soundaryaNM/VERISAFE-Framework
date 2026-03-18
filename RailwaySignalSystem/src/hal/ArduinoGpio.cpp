#include "railway/hal/ArduinoGpio.h"

#ifdef ARDUINO
#include <Arduino.h>
#endif

namespace railway::hal {

void ArduinoGpio::configure(Pin pin, PinMode mode) {
#ifdef ARDUINO
    switch (mode) {
        case PinMode::Input:
            ::pinMode(static_cast<int>(pin), INPUT);
            break;
        case PinMode::InputPullup:
            ::pinMode(static_cast<int>(pin), INPUT_PULLUP);
            break;
        case PinMode::OutputPushPull:
            ::pinMode(static_cast<int>(pin), OUTPUT);
            break;
    }
#else
    (void)pin;
    (void)mode;
#endif
}

PinLevel ArduinoGpio::read(Pin pin) const {
#ifdef ARDUINO
    return (::digitalRead(static_cast<int>(pin)) == HIGH) ? PinLevel::High : PinLevel::Low;
#else
    (void)pin;
    return PinLevel::Low;
#endif
}

void ArduinoGpio::write(Pin pin, PinLevel level) {
#ifdef ARDUINO
    ::digitalWrite(static_cast<int>(pin), (level == PinLevel::High) ? HIGH : LOW);
#else
    (void)pin;
    (void)level;
#endif
}

} // namespace railway::hal
