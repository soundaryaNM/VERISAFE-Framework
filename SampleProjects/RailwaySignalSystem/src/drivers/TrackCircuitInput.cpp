#include "railway/drivers/TrackCircuitInput.h"

namespace railway::drivers {

TrackCircuitInput::TrackCircuitInput(const Config& cfg, railway::hal::IGpio& gpio)
    : cfg_(cfg), gpio_(gpio) {}

void TrackCircuitInput::init() {
    gpio_.configure(cfg_.pin, railway::hal::PinMode::InputPullup);
    rawClear_ = readRawClear();
    stableClear_ = rawClear_;
    lastRawChangeMs_ = 0;
    lastUpdateMs_ = 0;
    healthy_ = true;
    stuckLowSinceMs_ = 0;
}

bool TrackCircuitInput::readRawClear() const {
    const auto level = gpio_.read(cfg_.pin);
    const bool rawHigh = (level == railway::hal::PinLevel::High);
    // activeLow means "Low" indicates clear/energized is false; we invert accordingly.
    // Clear means track circuit is energized.
    return cfg_.activeLow ? rawHigh : !rawHigh;
}

void TrackCircuitInput::update(railway::Millis nowMs) {
    lastUpdateMs_ = nowMs;

    const bool newRawClear = readRawClear();
    if (newRawClear != rawClear_) {
        rawClear_ = newRawClear;
        lastRawChangeMs_ = nowMs;
    }

    // Debounce: accept new state only after it remains stable long enough.
    if ((nowMs - lastRawChangeMs_) >= cfg_.debounceMs) {
        stableClear_ = rawClear_;
    }

    // Fault detection: track circuit stuck "not clear" (de-energized) beyond threshold.
    if (!stableClear_) {
        if (stuckLowSinceMs_ == 0) {
            stuckLowSinceMs_ = nowMs;
        }
        if ((nowMs - stuckLowSinceMs_) >= cfg_.stuckLowFaultMs) {
            healthy_ = false;
        }
    } else {
        stuckLowSinceMs_ = 0;
        healthy_ = true;
    }
}

bool TrackCircuitInput::isOccupied() const {
    // If not clear, treat as occupied (fail-safe).
    return !stableClear_;
}

bool TrackCircuitInput::isHealthy() const {
    return healthy_;
}

} // namespace railway::drivers
