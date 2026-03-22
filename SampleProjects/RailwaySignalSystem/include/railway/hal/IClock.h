#pragma once

#include "railway/Types.h"

namespace railway::hal {

class IClock {
public:
    virtual ~IClock() = default;
    virtual railway::Millis nowMs() const = 0;
};

} // namespace railway::hal
