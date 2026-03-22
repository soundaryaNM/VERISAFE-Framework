#include "railway/hal/IClock.h"

#include <chrono>

namespace railway::hal {

class SteadyClock final : public IClock {
public:
    railway::Millis nowMs() const override {
        const auto now = std::chrono::steady_clock::now();
        const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count();
        if (ms < 0) {
            return 0;
        }
        return static_cast<railway::Millis>(ms);
    }
};

IClock& steadyClockSingleton() {
    static SteadyClock clock;
    return clock;
}

} // namespace railway::hal
