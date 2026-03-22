#pragma once

#include "railway/Types.h"
#include "railway/hal/IGpio.h"

namespace railway::drivers {

enum class Aspect : std::uint8_t {
    Stop = 0,    // Red
    Caution = 1, // Yellow
    Clear = 2,   // Green
};

class SignalHead {
public:
    struct Config {
        railway::hal::Pin redPin{0};
        railway::hal::Pin yellowPin{0};
        railway::hal::Pin greenPin{0};
        bool activeHigh{true};
    };

    SignalHead(const Config& cfg, railway::hal::IGpio& gpio);

    void init();
    void setAspect(Aspect aspect);
    Aspect currentAspect() const;

private:
    void writeLamp(railway::hal::Pin pin, bool on);

    Config cfg_{};
    railway::hal::IGpio& gpio_;
    Aspect aspect_{Aspect::Stop};
};

} // namespace railway::drivers
