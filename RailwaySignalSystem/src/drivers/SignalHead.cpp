#include "railway/drivers/SignalHead.h"

namespace railway::drivers {

SignalHead::SignalHead(const Config& cfg, railway::hal::IGpio& gpio) : cfg_(cfg), gpio_(gpio) {}

void SignalHead::init() {
    gpio_.configure(cfg_.redPin, railway::hal::PinMode::OutputPushPull);
    gpio_.configure(cfg_.yellowPin, railway::hal::PinMode::OutputPushPull);
    gpio_.configure(cfg_.greenPin, railway::hal::PinMode::OutputPushPull);

    setAspect(Aspect::Stop);
}

void SignalHead::writeLamp(railway::hal::Pin pin, bool on) {
    const bool levelHigh = cfg_.activeHigh ? on : !on;
    gpio_.write(pin, levelHigh ? railway::hal::PinLevel::High : railway::hal::PinLevel::Low);
}

void SignalHead::setAspect(Aspect aspect) {
    // Fail-safe: any unknown value becomes STOP.
    if (aspect != Aspect::Stop && aspect != Aspect::Caution && aspect != Aspect::Clear) {
        aspect = Aspect::Stop;
    }

    aspect_ = aspect;

    // Never energize multiple lamps simultaneously (typical signalling requirement).
    writeLamp(cfg_.redPin, aspect_ == Aspect::Stop);
    writeLamp(cfg_.yellowPin, aspect_ == Aspect::Caution);
    writeLamp(cfg_.greenPin, aspect_ == Aspect::Clear);
}

Aspect SignalHead::currentAspect() const {
    return aspect_;
}

} // namespace railway::drivers
