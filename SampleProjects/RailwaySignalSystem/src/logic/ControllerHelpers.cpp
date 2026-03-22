#include "railway/logic/ControllerHelpers.h"

namespace railway::logic {

bool computeControllerFresh(railway::Millis lastTickMs, railway::Millis now, railway::Millis maxLoopGapMs) {
    if (lastTickMs == 0) {
        return true;
    }
    return (now - lastTickMs) <= maxLoopGapMs;
}

} // namespace railway::logic