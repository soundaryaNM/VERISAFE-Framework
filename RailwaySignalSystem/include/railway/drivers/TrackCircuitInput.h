#pragma once

#include "railway/Types.h"
#include "railway/hal/IGpio.h"

namespace railway::drivers {

// Track circuit input: energized (clear) vs de-energized (occupied/fault).
// This module does debouncing and basic "stuck-low" fault detection.
class TrackCircuitInput {
public:
    struct Config {
        railway::hal::Pin pin{0};
        bool activeLow{true};
        railway::Millis debounceMs{50};
        railway::Millis stuckLowFaultMs{3000};
    };

    explicit TrackCircuitInput(const Config& cfg, railway::hal::IGpio& gpio);

    void init();
    void update(railway::Millis nowMs);

    bool isOccupied() const;
    bool isHealthy() const;

private:
    bool readRawClear() const;

    Config cfg_{};
    railway::hal::IGpio& gpio_;

    bool rawClear_{true};
    bool stableClear_{true};
    railway::Millis lastRawChangeMs_{0};
    railway::Millis lastUpdateMs_{0};

    bool healthy_{true};
    railway::Millis stuckLowSinceMs_{0};
};

} // namespace railway::drivers
