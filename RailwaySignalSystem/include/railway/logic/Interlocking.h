#pragma once

#include "railway/Types.h"
#include "railway/drivers/SignalHead.h"

namespace railway::logic {

enum class StopReason : std::uint8_t {
    None = 0,
    OwnBlockOccupied = 1,
    DownstreamStop = 2,
    TrackCircuitFault = 3,
    ControllerStale = 4,
};

struct Inputs {
    bool ownBlockOccupied{true};
    bool downstreamBlockOccupied{true};
    bool ownTrackCircuitHealthy{false};
    bool controllerFresh{false};
};

struct Decision {
    railway::drivers::Aspect aspect{railway::drivers::Aspect::Stop};
    StopReason reason{StopReason::ControllerStale};
    railway::Health health{railway::Health::Fault};
};

// Pure logic interlocking decision.
Decision evaluate(const Inputs& in);

} // namespace railway::logic
