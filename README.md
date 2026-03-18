# UnitTestGen

A Python tool for generating unit tests automatically.

## Installation

```bash
pip install -e .
```

## Usage

# Add usage instructions here

## RailwaySignalSystem (C++ demo project)

This repo contains a small C++ project at `RailwaySignalSystem/` that is useful as a demo target for AI-assisted unit test generation.

### Build and run the simulation

```powershell
cmake -S RailwaySignalSystem -B RailwaySignalSystem/build
cmake --build RailwaySignalSystem/build -j
RailwaySignalSystem/build/railway_demo.exe
```

### Run unit tests (GoogleTest / gtest)

The project is set up to run tests via CTest (`ctest`). Tests are built only when GoogleTest is available.

#### Option A (recommended for client demos): install GoogleTest via vcpkg

1) Install vcpkg and integrate it with CMake (one-time on the demo machine).

2) Install gtest:

```powershell
vcpkg install gtest
```

3) Configure using the vcpkg toolchain file (replace the path):

```powershell
cmake -S RailwaySignalSystem -B RailwaySignalSystem/build `
	-DCMAKE_TOOLCHAIN_FILE=C:/path/to/vcpkg/scripts/buildsystems/vcpkg.cmake
cmake --build RailwaySignalSystem/build -j
ctest --test-dir RailwaySignalSystem/build --output-on-failure
```

#### Option B: provide GoogleTest from a local folder (offline-friendly)

If the demo environment cannot download dependencies from the internet, install/provide GoogleTest locally and configure CMake so `find_package(GTest)` can locate it by setting one of:

- `GTest_DIR` (preferred if you have a CMake package)
- `CMAKE_PREFIX_PATH` (points to the install prefix)

Then run:

```powershell
cmake -S RailwaySignalSystem -B RailwaySignalSystem/build -DCMAKE_PREFIX_PATH=C:/path/to/gtest/install
cmake --build RailwaySignalSystem/build -j
ctest --test-dir RailwaySignalSystem/build --output-on-failure
```

#### Note about corporate SSL

If you enable `-DRAILWAY_FETCH_GTEST=ON`, CMake will try to download googletest from GitHub. Some corporate machines block this (missing CA certs), which will fail the download. For client demos, prefer Option A or B above.

## VERISAFE Harness Architecture

- **Production repository is never modified.** All build and test artifacts are produced under `.verisafe/`.
- **Overlay harness:** VERISAFE generates a deterministic overlay at `.verisafe/` containing a `CMakeLists.txt`, `generated/` tests, and `extern/` vendored dependencies. The harness is created per-run and is non-invasive.
- **Deterministic CMake:** The harness CMake is generated deterministically and expects `-DREPO_ROOT=<repo_root>` when configuring; it will glob the production sources under `${REPO_ROOT}/src` and the generated tests under `${CMAKE_CURRENT_SOURCE_DIR}/generated`.
- **Build isolation:** CMake must always be invoked with `-S <repo>/.verisafe -B <repo>/.verisafe/build -DREPO_ROOT=<repo>` so the repository root is never used as a source directory.

This makes builds repeatable and prevents accidental changes to the production CMake configuration.