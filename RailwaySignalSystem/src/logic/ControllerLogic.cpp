#include "railway/logic/ControllerLogic.h"
#include "railway/logic/ControllerHelpers.h"

namespace railway::logic {

Decision evaluateControllerLogic(Millis lastTickMs, Millis now, Millis maxLoopGapMs,
                                 bool ownTrackCircuitHealthy, bool ownBlockOccupied, bool downstreamBlockOccupied) {
    const bool fresh = computeControllerFresh(lastTickMs, now, maxLoopGapMs);

    Inputs in{};
    in.controllerFresh = fresh;
    in.ownTrackCircuitHealthy = ownTrackCircuitHealthy;
    in.ownBlockOccupied = ownBlockOccupied;
    in.downstreamBlockOccupied = downstreamBlockOccupied;

    return evaluate(in);
}

} // namespace railway::logic