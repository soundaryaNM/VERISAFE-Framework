#pragma once

#include "railway/hal/IGpio.h"

#include <array>
#include <cstddef>

namespace railway::hal {

class MockGpio final : public IGpio {
public:
    void configure(Pin pin, PinMode mode) override;
    PinLevel read(Pin pin) const override;
    void write(Pin pin, PinLevel level) override;

    // Drives the level that will be observed by read(). Useful for simulation/tests.
    void setInputLevel(Pin pin, PinLevel level);

private:
    static constexpr std::size_t kMaxPins = 256;

    std::array<PinMode, kMaxPins> modes_{};
    std::array<PinLevel, kMaxPins> levels_{};
};

} // namespace railway::hal
