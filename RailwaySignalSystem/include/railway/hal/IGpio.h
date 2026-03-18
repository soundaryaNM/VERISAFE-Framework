#pragma once

#include <cstdint>

namespace railway::hal {

enum class PinLevel : std::uint8_t {
    Low = 0,
    High = 1,
};

enum class PinMode : std::uint8_t {
    Input = 0,
    InputPullup = 1,
    OutputPushPull = 2,
};

using Pin = std::uint16_t;

class IGpio {
public:
    virtual ~IGpio() = default;

    virtual void configure(Pin pin, PinMode mode) = 0;
    virtual PinLevel read(Pin pin) const = 0;
    virtual void write(Pin pin, PinLevel level) = 0;
};

} // namespace railway::hal
