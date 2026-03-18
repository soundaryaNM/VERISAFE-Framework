#pragma once

#include "railway/Types.h"
#include "railway/drivers/SignalHead.h"
#include "railway/drivers/TrackCircuitInput.h"
#include "railway/hal/IClock.h"
#include "railway/logic/Interlocking.h"

namespace railway::app {

// Mixed hardware + logic controller for a single block.
class BlockController {
public:
    struct Config {
        railway::Millis maxLoopGapMs{200};
    };

    BlockController(const Config& cfg,
                    railway::hal::IClock& clock,
                    railway::drivers::TrackCircuitInput& ownTrack,
                    railway::drivers::TrackCircuitInput& downstreamTrack,
                    railway::drivers::SignalHead& signal);

    void init();
    void tick();

    railway::logic::Decision lastDecision() const;

private:
    Config cfg_{};
    railway::hal::IClock& clock_;
    railway::drivers::TrackCircuitInput& ownTrack_;
    railway::drivers::TrackCircuitInput& downstreamTrack_;
    railway::drivers::SignalHead& signal_;

    railway::Millis lastTickMs_{0};
    railway::logic::Decision last_{};
};

} // namespace railway::app
