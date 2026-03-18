file(GLOB_RECURSE RAILWAY_LOGIC_SOURCES CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/logic/*.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/drivers/*.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/hal/*.cpp"
)

# Include app modules that are safe for unit testing, while excluding entry points.
list(APPEND RAILWAY_LOGIC_SOURCES
    "${CMAKE_CURRENT_SOURCE_DIR}/app/BlockController.cpp"
)

# Intentionally exclude src/app/* (may define main() / platform entry points).
add_library(railway_logic ${RAILWAY_LOGIC_SOURCES})

target_include_directories(railway_logic PUBLIC
    ${CMAKE_SOURCE_DIR}/include
)

target_link_libraries(railway_logic PUBLIC
    # Add any dependencies here
)
