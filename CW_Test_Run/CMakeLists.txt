cmake_minimum_required(VERSION 3.14)
project(CTestRunner CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

enable_testing()

# Define paths - assuming stubs are in stubs/ and gtest in gtest/
# In a real repo, these should probably be in a lib/ or test/support/ folder
set(TEST_SUPPORT_DIR ${CMAKE_CURRENT_SOURCE_DIR}/stubs) 
set(GTEST_DIR ${CMAKE_CURRENT_SOURCE_DIR}/gtest)
set(STUBS_DIR ${TEST_SUPPORT_DIR})

# Include directories
include_directories(${CMAKE_CURRENT_SOURCE_DIR})
include_directories(${GTEST_DIR})
include_directories(${STUBS_DIR})

# Helper function to add a test
function(add_component_test component_name)
    set(SOURCE_FILE ${CMAKE_CURRENT_SOURCE_DIR}/../${component_name}.cpp)
    set(TEST_FILE ${CMAKE_CURRENT_SOURCE_DIR}/test_${component_name}.cpp)

    if(EXISTS ${TEST_FILE})
        message(STATUS "Adding test target: test_${component_name}")
        add_executable(test_${component_name}
            ${TEST_FILE}
            ${SOURCE_FILE}
            ${STUBS_DIR}/Arduino_stubs.cpp
            ${STUBS_DIR}/HTTPClient.cpp
            ${STUBS_DIR}/SPIFFS.cpp
        )
        
        # Add include directories specifically for this target
        target_include_directories(test_${component_name} PRIVATE 
            ${CMAKE_CURRENT_SOURCE_DIR}/..
            ${GTEST_DIR}
            ${STUBS_DIR}
        )

        add_test(NAME test_${component_name} COMMAND test_${component_name})
    else()
        message(STATUS "Test file not found for ${component_name}: ${TEST_FILE}")
    endif()
endfunction()

# Add tests for known components - this will be dynamically generated
# For now, placeholder
# add_component_test(c_led)
# add_component_test(c_blynk)
# add_component_test(cspiffs)
