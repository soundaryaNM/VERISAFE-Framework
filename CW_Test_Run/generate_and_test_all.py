import os
import subprocess
import sys
import glob
import shutil
import argparse

def main():
    parser = argparse.ArgumentParser(description="Generate and run C++ tests for a repository")
    parser.add_argument('--repo-path', required=True, help='Path to the C++ repository to test')
    parser.add_argument('--generator-path', default=r'c:\Users\SwathantraPulicherla\workspaces\CPP\CW_Test_Gen', help='Path to the test generator')
    args = parser.parse_args()

    REPO_ROOT = os.path.abspath(args.repo_path)
    GENERATOR_DIR = os.path.abspath(args.generator_path)
    BUILD_DIR = os.path.join(REPO_ROOT, "build")
    TESTS_DIR = os.path.join(REPO_ROOT, "tests")
    
    # Check API Key
    if "GEMINI_API_KEY" not in os.environ:
        print("Error: GEMINI_API_KEY environment variable not set.")
        print("Please set it before running this script.")
        sys.exit(1)

    # 1. Generate Tests
    print("\n=== Step 1: Generating Tests ===")
    source_files = [f for f in os.listdir(REPO_ROOT) if f.endswith('.cpp') and not f.startswith('test_')]
    
    for src_file in source_files:
        print(f"Processing {src_file}...")
        cmd = [
            sys.executable, "-m", "ai_c_test_generator.cli",
            "--repo-path", REPO_ROOT,
            "--source-dir", ".",
            "--file", src_file,
            "--output", TESTS_DIR
        ]
        
        try:
            subprocess.run(cmd, cwd=GENERATOR_DIR, check=True)
        except subprocess.CalledProcessError as e:
            print(f"Failed to generate test for {src_file}: {e}")
            continue

    # 2. Configure CMake
    print("\n=== Step 2: Configuring CMake ===")
    if os.path.exists(BUILD_DIR):
        shutil.rmtree(BUILD_DIR)
    os.makedirs(BUILD_DIR, exist_ok=True)
    
    # Copy stubs and gtest from CW_Test_Run to repo build directory
    cw_test_run_dir = os.path.dirname(os.path.abspath(__file__))
    stubs_src = os.path.join(cw_test_run_dir, "stubs")
    gtest_src = os.path.join(cw_test_run_dir, "gtest")
    
    if os.path.exists(stubs_src):
        shutil.copytree(stubs_src, os.path.join(BUILD_DIR, "stubs"))
    if os.path.exists(gtest_src):
        shutil.copytree(gtest_src, os.path.join(BUILD_DIR, "gtest"))
    
    # Generate CMakeLists.txt dynamically
    cmake_content = f"""cmake_minimum_required(VERSION 3.14)
project(CTestRunner CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

enable_testing()

# Define paths
set(TEST_SUPPORT_DIR ${{CMAKE_CURRENT_SOURCE_DIR}}/../stubs) 
set(GTEST_DIR ${{CMAKE_CURRENT_SOURCE_DIR}}/../gtest)
set(STUBS_DIR ${{TEST_SUPPORT_DIR}})

# Include directories
include_directories(${{CMAKE_CURRENT_SOURCE_DIR}}/../)
include_directories(${{GTEST_DIR}})
include_directories(${{STUBS_DIR}})

# Helper function to add a test
function(add_component_test component_name)
    set(SOURCE_FILE ${{CMAKE_CURRENT_SOURCE_DIR}}/../../{component_name}.cpp)
    set(TEST_FILE ${{CMAKE_CURRENT_SOURCE_DIR}}/../test_{component_name}.cpp)

    if(EXISTS ${{TEST_FILE}})
        message(STATUS "Adding test target: test_${{component_name}}")
        add_executable(test_${{component_name}}
            ${{TEST_FILE}}
            ${{SOURCE_FILE}}
            ${{STUBS_DIR}}/Arduino_stubs.cpp
            ${{STUBS_DIR}}/HTTPClient.cpp
            ${{STUBS_DIR}}/SPIFFS.cpp
        )
        
        # Add include directories specifically for this target
        target_include_directories(test_${{component_name}} PRIVATE 
            ${{CMAKE_CURRENT_SOURCE_DIR}}/../..
            ${{GTEST_DIR}}
            ${{STUBS_DIR}}
        )

        add_test(NAME test_${{component_name}} COMMAND test_${{component_name}})
    else()
        message(STATUS "Test file not found for ${{component_name}}: ${{TEST_FILE}}")
    endif()
endfunction()

"""
    
    # Add tests for each source file
    for src_file in source_files:
        component_name = os.path.splitext(src_file)[0]
        cmake_content += f"add_component_test({component_name})\n"
    
    cmake_path = os.path.join(BUILD_DIR, "CMakeLists.txt")
    with open(cmake_path, 'w') as f:
        f.write(cmake_content)
    
    cmd_cmake = ["cmake", "."]
    try:
        subprocess.run(cmd_cmake, cwd=BUILD_DIR, check=True)
    except subprocess.CalledProcessError as e:
        print(f"CMake configuration failed: {e}")
        sys.exit(1)

    # 3. Build
    print("\n=== Step 3: Building Tests ===")
    cmd_build = ["cmake", "--build", "."]
    try:
        subprocess.run(cmd_build, cwd=BUILD_DIR, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Build failed: {e}")
        sys.exit(1)

    # 4. Run Tests
    print("\n=== Step 4: Running Tests ===")
    cmd_test = ["ctest", "--output-on-failure"]
    try:
        subprocess.run(cmd_test, cwd=BUILD_DIR, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Tests failed: {e}")
        sys.exit(1)

    print("\n=== All Steps Completed Successfully ===")

if __name__ == "__main__":
    main()
