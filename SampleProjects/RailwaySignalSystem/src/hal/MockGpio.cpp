#include "railway/hal/MockGpio.h"

namespace railway::hal {

void MockGpio::configure(Pin pin, PinMode mode) {
    if (pin < kMaxPins) {
        modes_[pin] = mode;
    }
}

PinLevel MockGpio::read(Pin pin) const {
    if (pin < kMaxPins) {
        return levels_[pin];
    }
    return PinLevel::Low;
}

void MockGpio::write(Pin pin, PinLevel level) {
    if (pin < kMaxPins) {
        levels_[pin] = level;
    }
}

void MockGpio::setInputLevel(Pin pin, PinLevel level) {
    if (pin < kMaxPins) {
        levels_[pin] = level;
    }
}

// Factory for demo builds; in embedded builds you'd provide a real implementation.
IGpio& mockGpioSingleton() {
    static MockGpio gpio;
    return gpio;
}

} // namespace railway::hal
