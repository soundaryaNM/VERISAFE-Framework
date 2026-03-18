#pragma once

#include "railway/Types.h"

namespace railway::logic {

// Pure function to compute controller freshness based on timing.
bool computeControllerFresh(railway::Millis lastTickMs, railway::Millis now, railway::Millis maxLoopGapMs);

} // namespace railway::logic