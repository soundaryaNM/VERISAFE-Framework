#include "railway/logic/Interlocking.h"

namespace railway::logic {

Decision evaluate(const Inputs& in) {
    Decision out{};

    // Fail-safe first.
    if (!in.controllerFresh) {
        out.aspect = railway::drivers::Aspect::Stop;
        out.reason = StopReason::ControllerStale;
        out.health = railway::Health::Fault;
        return out;
    }

    if (!in.ownTrackCircuitHealthy) {
        out.aspect = railway::drivers::Aspect::Stop;
        out.reason = StopReason::TrackCircuitFault;
        out.health = railway::Health::Degraded;
        return out;
    }

    if (in.ownBlockOccupied) {
        out.aspect = railway::drivers::Aspect::Stop;
        out.reason = StopReason::OwnBlockOccupied;
        out.health = railway::Health::Ok;
        return out;
    }

    // Approach control / simple two-block logic.
    if (in.downstreamBlockOccupied) {
        out.aspect = railway::drivers::Aspect::Caution;
        out.reason = StopReason::DownstreamStop;
        out.health = railway::Health::Ok;
        return out;
    }

    out.aspect = railway::drivers::Aspect::Clear;
    out.reason = StopReason::None;
    out.health = railway::Health::Ok;
    return out;
}

} // namespace railway::logic
