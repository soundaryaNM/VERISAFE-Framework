#include "railway/app/BlockController.h"
#include "railway/logic/ControllerLogic.h"

namespace railway::app {

BlockController::BlockController(const Config& cfg,
                                 railway::hal::IClock& clock,
                                 railway::drivers::TrackCircuitInput& ownTrack,
                                 railway::drivers::TrackCircuitInput& downstreamTrack,
                                 railway::drivers::SignalHead& signal)
    : cfg_(cfg),
      clock_(clock),
      ownTrack_(ownTrack),
      downstreamTrack_(downstreamTrack),
      signal_(signal) {}

void BlockController::init() {
    ownTrack_.init();
    downstreamTrack_.init();
    signal_.init();

    lastTickMs_ = clock_.nowMs();
    last_ = railway::logic::evaluate(railway::logic::Inputs{});
    signal_.setAspect(last_.aspect);
}

void BlockController::tick() {
    const auto now = clock_.nowMs();

    ownTrack_.update(now);
    downstreamTrack_.update(now);

    last_ = railway::logic::evaluateControllerLogic(lastTickMs_, now, cfg_.maxLoopGapMs,
                                                     ownTrack_.isHealthy(), ownTrack_.isOccupied(), downstreamTrack_.isOccupied());
    lastTickMs_ = now;
    signal_.setAspect(last_.aspect);
}

railway::logic::Decision BlockController::lastDecision() const {
    return last_;
}

} // namespace railway::app
