#pragma once

#include "railway/Types.h"
#include "railway/logic/Interlocking.h"

namespace railway::logic {

// Higher-level controller logic that combines freshness check with interlocking evaluation.
// This is pure: takes inputs and returns decision.
Decision evaluateControllerLogic(Millis lastTickMs, Millis now, Millis maxLoopGapMs,
                                 bool ownTrackCircuitHealthy, bool ownBlockOccupied, bool downstreamBlockOccupied);

} // namespace railway::logic