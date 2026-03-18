#include "railway/hal/PlatformHal.h"

namespace railway::hal {

// Implementations are provided by host singletons.
IGpio& gpio() {
    extern IGpio& mockGpioSingleton();
    return mockGpioSingleton();
}

IClock& clock() {
    extern IClock& steadyClockSingleton();
    return steadyClockSingleton();
}

} // namespace railway::hal
