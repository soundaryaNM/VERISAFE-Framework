#pragma once

#include "railway/hal/IClock.h"
#include "railway/hal/IGpio.h"

namespace railway::hal {

// Host/embedded selection happens at link-time.
IGpio& gpio();
IClock& clock();

} // namespace railway::hal
