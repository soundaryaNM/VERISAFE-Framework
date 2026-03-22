#include "railway/app/BlockController.h"
#include "railway/drivers/SignalHead.h"
#include "railway/drivers/TrackCircuitInput.h"
#include "railway/hal/MockGpio.h"
#include "railway/hal/PlatformHal.h"

#include <chrono>
#include <iostream>
#include <thread>

namespace {

const char* toString(railway::drivers::Aspect a) {
    switch (a) {
        case railway::drivers::Aspect::Stop:
            return "STOP";
        case railway::drivers::Aspect::Caution:
            return "CAUTION";
        case railway::drivers::Aspect::Clear:
            return "CLEAR";
    }
    return "STOP";
}

const char* toString(railway::logic::StopReason r) {
    switch (r) {
        case railway::logic::StopReason::None:
            return "None";
        case railway::logic::StopReason::OwnBlockOccupied:
            return "OwnBlockOccupied";
        case railway::logic::StopReason::DownstreamStop:
            return "DownstreamStop";
        case railway::logic::StopReason::TrackCircuitFault:
            return "TrackCircuitFault";
        case railway::logic::StopReason::ControllerStale:
            return "ControllerStale";
    }
    return "ControllerStale";
}

} // namespace

int app_main() {
    auto& gpio = railway::hal::gpio();
    auto& clock = railway::hal::clock();

    // Host simulation hook: allow driving input pins.
    auto* mock = dynamic_cast<railway::hal::MockGpio*>(&gpio);

    railway::drivers::TrackCircuitInput::Config ownCfg;
    ownCfg.pin = 2;
    ownCfg.activeLow = true;
    // Demo-friendly timing: short debounce and fault so you can observe state changes quickly.
    ownCfg.debounceMs = 50;
    ownCfg.stuckLowFaultMs = 800;
    railway::drivers::TrackCircuitInput own(ownCfg, gpio);

    railway::drivers::TrackCircuitInput::Config nextCfg;
    nextCfg.pin = 3;
    nextCfg.activeLow = true;
    nextCfg.debounceMs = 50;
    nextCfg.stuckLowFaultMs = 800;
    railway::drivers::TrackCircuitInput next(nextCfg, gpio);

    railway::drivers::SignalHead::Config sigCfg;
    sigCfg.redPin = 10;
    sigCfg.yellowPin = 11;
    sigCfg.greenPin = 12;
    sigCfg.activeHigh = true;
    railway::drivers::SignalHead signal(sigCfg, gpio);

    railway::app::BlockController::Config ctrlCfg;
    ctrlCfg.maxLoopGapMs = 200;
    railway::app::BlockController controller(ctrlCfg, clock, own, next, signal);
    controller.init();

    if (mock != nullptr) {
        // activeLow=true in TrackCircuitInput means: HIGH == clear, LOW == occupied/fault.
        mock->setInputLevel(2, railway::hal::PinLevel::High);
        mock->setInputLevel(3, railway::hal::PinLevel::High);
    }

    auto lastAspect = controller.lastDecision().aspect;
    auto lastReason = controller.lastDecision().reason;
    std::cout << "t=0ms aspect=" << toString(lastAspect) << " reason=" << toString(lastReason) << "\n";

    // 50ms tick like a typical embedded superloop.
    for (int i = 0; i < 80; ++i) {
        const auto tMs = static_cast<int>(i * 50);

        // Scenario timeline (host simulation only):
        // - 0ms: both clear
        // - 600ms: downstream becomes occupied -> CAUTION
        // - 1300ms: own becomes occupied -> STOP
        // - 1900ms: own clears again -> CAUTION
        // - 2500ms: downstream clears -> CLEAR
        // - 3000ms+: induce a track circuit "fault" by holding own not-clear long enough
        if (mock != nullptr) {
            if (tMs == 600) {
                mock->setInputLevel(3, railway::hal::PinLevel::Low);
            }
            if (tMs == 1300) {
                mock->setInputLevel(2, railway::hal::PinLevel::Low);
            }
            if (tMs == 1900) {
                mock->setInputLevel(2, railway::hal::PinLevel::High);
            }
            if (tMs == 2500) {
                mock->setInputLevel(3, railway::hal::PinLevel::High);
            }
            if (tMs == 3000) {
                mock->setInputLevel(2, railway::hal::PinLevel::Low);
            }
        }

        controller.tick();

        const auto d = controller.lastDecision();
        if (d.aspect != lastAspect || d.reason != lastReason) {
            lastAspect = d.aspect;
            lastReason = d.reason;
            std::cout << "t=" << tMs << "ms aspect=" << toString(d.aspect) << " reason=" << toString(d.reason) << "\n";
        }

        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }

    return 0;
}
