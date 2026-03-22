#pragma once

#include <cstdint>

namespace railway {

using Millis = std::uint32_t;

enum class Health : std::uint8_t {
    Ok = 0,
    Degraded = 1,
    Fault = 2,
};

struct Timestamp {
    Millis ms{0};
};

} // namespace railway
