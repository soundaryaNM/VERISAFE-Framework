"""
Test Validator - Validates generated test files
"""

import os
import re
import sys
import datetime
from typing import Dict, List

from ai_c_test_analyzer.analyzer import DependencyAnalyzer


class TestValidator:
    """Universal C Test File Validator - Repo Independent"""

    def __init__(self, repo_path: str):
        self.repo_path = repo_path
        self.analyzer = DependencyAnalyzer(repo_path)

    def validate_test_file(self, test_file_path: str, source_file_path: str) -> Dict:
        """
        Validate a generated test file against its source file using comprehensive criteria
        """
        validation_result = {
            'file': os.path.basename(test_file_path),
            'test_file': test_file_path,
            'source_file': source_file_path,
            'source_rel': None,
            'compiles': True,
            'realistic': True,
            'quality': 'High',
            'issues': [],
            'keep': [],
            'fix': [],
            'remove': []
        }

        # Best-effort: record repo-relative source path so reports can mirror source structure.
        try:
            validation_result['source_rel'] = os.path.relpath(os.path.abspath(source_file_path), os.path.abspath(self.repo_path)).replace('\\', '/')
        except Exception:
            validation_result['source_rel'] = None

        try:
            # Read both files
            with open(test_file_path, 'r') as f:
                test_content = f.read()
            with open(source_file_path, 'r') as f:
                source_content = f.read()

            # Detect framework
            is_gtest = '#include <gtest/gtest.h>' in test_content or '#include "gtest/gtest.h"' in test_content
            is_unity = '#include "unity.h"' in test_content or '#include <unity.h>' in test_content

            # Extract source function signatures
            source_functions = self.analyzer._extract_functions(source_file_path)
            source_includes = self.analyzer._extract_includes(source_file_path)

            # 1. COMPILATION SAFETY CHECKS
            self._check_compilation_safety(test_content, source_functions, source_includes, validation_result, is_gtest)

            # 2. REALITY CHECKS
            self._check_reality_tests(test_content, source_functions, validation_result, is_gtest)

            # 3. TEST QUALITY ASSESSMENT
            self._assess_test_quality(test_content, source_functions, validation_result, is_gtest)

            # 4. LOGICAL CONSISTENCY VERIFICATION
            self._verify_logical_consistency(test_content, validation_result, is_gtest)

            # 5. EMBEDDED HARDWARE VALIDATION
            self._check_embedded_features(test_content, source_content, validation_result)

            # Determine overall quality rating
            validation_result['quality'] = self._calculate_quality_rating(validation_result)

        except Exception as e:
            validation_result['issues'].append(f"Validation error: {str(e)}")
            validation_result['compiles'] = False
            validation_result['quality'] = 'Low'

        return validation_result

    def _check_compilation_safety(self, test_content: str, source_functions: List[Dict], source_includes: List[str], result: Dict, is_gtest: bool = False):
        """Check compilation safety criteria"""

        # Check for markdown markers (should be removed by post-processing)
        if '```' in test_content:
            result['issues'].append("Found markdown code block markers (```) - should be removed")
            result['compiles'] = False

        # Check for required includes
        if is_gtest:
            required_includes = ['gtest/gtest.h']
            # For C++ GTest, we might not need to include the source header if we are testing a .cpp file directly
            # or if the source code is embedded in the test file (which is common for single-file generation)
        else:
            required_includes = ['unity.h'] + [f"{func['name']}.h" for func in source_functions if func['name'] != 'main']

        for include in required_includes:
            if f'#include "{include}"' not in test_content and f'#include <{include}>' not in test_content:
                if include == 'unity.h':
                    result['issues'].append(f"Missing required Unity include: #include \"unity.h\"")
                    result['compiles'] = False
                elif include == 'gtest/gtest.h':
                    result['issues'].append(f"Missing required Google Test include: #include <gtest/gtest.h>")
                    result['compiles'] = False

        # Repo integration rule: when linking against gtest_main, tests must NOT define main().
        # (Defining main in the test file will cause duplicate symbol / link errors.)
        if is_gtest and re.search(r'\bint\s+main\s*\(', test_content):
            result['issues'].append("GoogleTest file defines int main(...). This repo links gtest_main; remove the custom main().")
            result['compiles'] = False

        # V2 section wrapper guard:
        # Generated tests are wrapped in `namespace ai_test_section_<id> { ... }`.
        # Declaring qualified production namespaces *inside* that wrapper can accidentally create
        # `ai_testgen_section_<id>::<project>::...` instead of `::<project>::...`.
        if is_gtest and ('namespace ai_testgen_section_' in test_content or 'namespace ai_test_section_' in test_content):
            if re.search(r'\bnamespace\s+[A-Za-z_][A-Za-z0-9_]*::', test_content):
                result['issues'].append(
                    "Do not declare qualified production namespaces inside an ai_testgen_section. Use fully-qualified references (`::<project>::...`) and keep fakes in a neutral namespace (e.g., `test_support`)."
                )
                result['compiles'] = False

            # If a fixture exists, prefer TEST_F everywhere (avoids generator producing TEST by mistake).
            has_fixture = bool(re.search(r'\bclass\s+\w+\s*:\s*public\s+(?:::)?testing::Test\b', test_content))
            if has_fixture and re.search(r'\bTEST\s*\(', test_content) and not re.search(r'\bTEST_F\s*\(', test_content):
                result['issues'].append("Fixture detected but tests use TEST() instead of TEST_F().")
                result['compiles'] = False

        # Check for invalid includes (headers that don't exist)
        invalid_includes = []
        include_pattern = re.compile(r'#include\s+["<]([^">]+)[">]')
        # Common standard C library headers that are acceptable in tests
        standard_headers = {
            'stdio.h', 'stdlib.h', 'string.h', 'math.h', 'assert.h', 'ctype.h', 'errno.h', 'limits.h', 'stdarg.h', 'stddef.h', 'stdint.h', 'stdbool.h', 'time.h',
            'iostream', 'string', 'vector', 'map', 'algorithm', 'array', 'utility', 'tuple', 'memory', 'type_traits', 'limits', 'cstdint', 'cstddef', 'cstring',
        }
        
        for match in include_pattern.finditer(test_content):
            header = match.group(1)
            # Allow unity.h, gtest, standard library headers, and headers from source
            if header != 'unity.h' and header != 'gtest/gtest.h' and header != 'gmock/gmock.h' and header not in standard_headers and header not in source_includes:
                # Check if it's a valid header file that should exist
                if not any(header in inc for inc in source_includes):
                    invalid_includes.append(header)

        if invalid_includes:
            # For GTest/C++, we might be more lenient with includes if they look like standard C++ headers
            if not is_gtest:
                result['issues'].append(f"Invalid includes for non-existent headers: {invalid_includes}")
                result['compiles'] = False

        # Check function signature matches
        test_functions = self._extract_test_functions(test_content)
        for test_func in test_functions:
            if 'test_' in test_func['name']:
                # Check if stub functions match source signatures
                stub_matches = re.findall(r'(\w+)\s+(\w+)\s*\([^)]*\)\s*\{', test_content)
                for return_type, func_name in stub_matches:
                    # Find matching source function
                    source_match = next((f for f in source_functions if f['name'] == func_name), None)
                    if source_match and source_match['return_type'] != return_type:
                        result['issues'].append(f"Stub function {func_name} return type mismatch: {return_type} vs {source_match['return_type']}")
                        result['compiles'] = False

        # Check for duplicate symbols
        function_names = [f['name'] for f in test_functions]
        if len(function_names) != len(set(function_names)):
            duplicates = [name for name in function_names if function_names.count(name) > 1]
            result['issues'].append(f"Duplicate function definitions: {duplicates}")
            result['compiles'] = False

        # Check for invalid lambda assignment to member functions (Monkey-patching attempt)
        # Pattern: ->.* = \[
        if re.search(r'->\w+\s*=\s*\[', test_content):
            result['issues'].append("CRITICAL SYNTAX ERROR: Attempted to assign lambda to member function (monkey-patching). C++ DOES NOT SUPPORT THIS. Use global stubs instead.")
            result['compiles'] = False

        # Common C++ hallucinations observed in generated tests.
        if is_gtest:
            if re.search(r'\bPinMode::OutputOpenDrain\b', test_content) or re.search(r'\bPinMode::Analog\b', test_content):
                result['issues'].append("Hallucinated PinMode enum values (e.g., OutputOpenDrain/Analog). Use only enum members that exist in the real header.")
                result['compiles'] = False

            # If a fake inherits IGpio, it must implement read(Pin) const.
            if re.search(r'class\s+\w+\s*:\s*public\s+(?:::)?[A-Za-z_][A-Za-z0-9_]*::hal::IGpio\b', test_content):
                # Either implement read(...) const override, or the class remains abstract.
                if not re.search(r'\bread\s*\(\s*(?:::)?[A-Za-z_][A-Za-z0-9_]*::hal::Pin\b[^\)]*\)\s*const\s+override', test_content):
                    result['issues'].append("IGpio-derived fake missing required override: PinLevel read(Pin) const.")
                    result['compiles'] = False

            # Likely-private helper calls frequently break compilation.
            if re.search(r'\bwriteLamp\s*\(', test_content):
                result['issues'].append("Calls private helper writeLamp(...). Test indirectly via public APIs (init/setAspect/etc.).")
                result['compiles'] = False

        # Check for invalid function calls (like main()) - but allow if main() is simple and testable
        # Only flag actual calls to main(), not the test runner's main() function definition
        main_calls = re.findall(r'\bmain\s*\([^)]*\)\s*;', test_content)
        if main_calls:
            # Allow main() testing if it's declared as extern and called like a regular function
            # This is acceptable for simple main functions that don't have complex setup
            # Allow both 'extern int main(void);' and 'extern int main();'
            if not re.search(r'extern\s+int\s+main\s*\(\s*(?:void)?\s*\)\s*;', test_content):
                result['issues'].append("Invalid call to main() function - not suitable for unit testing")
                result['compiles'] = False

    def _check_reality_tests(self, test_content: str, source_functions: List[Dict], result: Dict, is_gtest: bool = False):
        """Validate reality checks"""

        # Safety review policy: generated tests must not contain speculative language.
        if re.search(r"\b(?:assum(?:e|ing|ption)s?|plausible|likely)\b", test_content, re.IGNORECASE):
            result['issues'].append(
                "Speculative language found (assume/assuming/assumption/plausible/likely). Generated tests must not make assumptions; assert only behavior supported by source/headers or compute expected values via real repo functions."
            )
            result['compiles'] = False
            result['realistic'] = False

        # Check for invalid floating point assertions
        if not is_gtest and 'TEST_ASSERT_EQUAL_FLOAT' in test_content:
            result['issues'].append("TEST_ASSERT_EQUAL_FLOAT used - will fail due to precision. Use TEST_ASSERT_FLOAT_WITHIN instead")
            result['realistic'] = False
        
        if is_gtest and 'ASSERT_FLOAT_EQ' in test_content and 'EXPECT_NEAR' not in test_content:
             # GTest ASSERT_FLOAT_EQ is generally okay, but EXPECT_NEAR is better for embedded
             pass

        # Check for impossible test values
        impossible_patterns = [
            (r'-?273\.15f?', 'Absolute zero temperature test - physically impossible'),
            (r'1e10+', 'Extremely large values that may cause overflow'),
            (r'NULL.*=.*[^=!].*NULL', 'Testing NULL assignments that may crash'),
        ]

        lines = test_content.split('\n')
        for i, line in enumerate(lines, 1):
            for pattern, description in impossible_patterns:
                if re.search(pattern, line):
                    result['issues'].append(f"Line {i}: {description} - unrealistic test scenario")
                    result['realistic'] = False

        # Check floating point comparisons have tolerance - only for actual assertions
        if not is_gtest:
            float_assertions = re.findall(r'TEST_ASSERT_FLOAT_WITHIN\s*\([^)]+\)', test_content)
            float_equal_assertions = re.findall(r'TEST_ASSERT_EQUAL_FLOAT\s*\([^)]+\)', test_content)

            # Only flag if there are float equality assertions without tolerance
            if float_equal_assertions and not float_assertions:
                result['issues'].append("TEST_ASSERT_EQUAL_FLOAT used - will fail due to precision. Use TEST_ASSERT_FLOAT_WITHIN instead")
                result['realistic'] = False

        # Check stub return types match expected ranges - be more specific about context
        if 'temperature' in test_content.lower() or 'celsius' in test_content.lower():
            # Temperature should be reasonable range for the specific sensor
            # Look for actual temperature assignments, not raw ADC values
            temp_assignment_patterns = [
                r'return_value\s*=\s*(\d+\.?\d*)f?',  # stub return values
                r'TEST_ASSERT_FLOAT_WITHIN\s*\([^,]+,\s*(\d+\.?\d*)f?',  # float assertions
                r'EXPECT_NEAR\s*\([^,]+,\s*(\d+\.?\d*)f?', # GTest float assertions
                r'(\d+\.?\d*)f?\s*,\s*temp',  # temperature parameters
            ]

            for pattern in temp_assignment_patterns:
                matches = re.findall(pattern, test_content)
                for val in matches:
                    try:
                        temp = float(val)
                        # Skip validation for raw ADC values (0-1023 range) that are clearly for rand() stubs
                        if temp >= 0 and temp <= 1023:
                            # Check if this is clearly a raw ADC value by looking at context
                            line_context = ""
                            for line in test_content.split('\n'):
                                if val in line and ('rand' in line.lower() or 'stub_rand' in line or 'return_value' in line):
                                    line_context = line.lower()
                                    break

                            if 'rand' in line_context or 'stub_rand' in line_context or 'return_value' in line_context:
                                continue  # This is a raw ADC value for rand(), not a temperature

                        # Validate temperature ranges
                        if temp > 200.0:  # Definitely too high for temperature
                            result['issues'].append(f"Temperature value {temp} seems unreasonably high (valid range: -40C to 125C)")
                            result['realistic'] = False
                        elif temp < -100.0:  # Definitely too low for temperature
                            result['issues'].append(f"Temperature value {temp} seems unreasonably low (valid range: -40C to 125C)")
                            result['realistic'] = False
                    except ValueError:
                        pass

    def _assess_test_quality(self, test_content: str, source_functions: List[Dict], result: Dict, is_gtest: bool = False):
        """Assess test quality criteria"""

        test_functions = self._extract_test_functions(test_content)
        
        if is_gtest:
            # For GTest, look for TEST() and TEST_F() macros
            test_names = re.findall(r'TEST(?:_F)?\s*\(\s*(\w+)\s*,\s*(\w+)\s*\)', test_content)
            # Flatten to just test names
            test_names = [f"{suite}_{name}" for suite, name in test_names]
        else:
            test_names = [f['name'] for f in test_functions if f['name'].startswith('test_')]

        # Check for edge cases
        edge_case_indicators = ['min', 'max', 'zero', 'negative', 'boundary', 'edge', 'limit']
        has_edge_cases = any(any(indicator in name.lower() for indicator in edge_case_indicators) for name in test_names)

        if not has_edge_cases and len(test_names) > 1:
            result['issues'].append("Missing edge case tests (min/max values, boundaries)")

        # Check for error conditions
        error_indicators = ['error', 'fail', 'invalid', 'null', 'boundary']
        has_error_tests = any(any(indicator in name.lower() for indicator in error_indicators) for name in test_names)

        # Check setUp/tearDown usage
        if is_gtest:
            has_setup = 'SetUp' in test_content
            has_teardown = 'TearDown' in test_content
        else:
            has_setup = 'setUp(' in test_content
            has_teardown = 'tearDown(' in test_content

        if has_setup and has_teardown:
            # Check if there are stub variables that need resetting
            # Stub variables typically start with 'g_' and are used for call counts/return values
            stub_variables = re.findall(r'static\s+\w+\s+g_\w+;', test_content)
            
            if stub_variables:  # Only require tearDown resets if there are actual stub variables
                # Verify stubs are reset - check for either reset functions or direct variable resets
                has_reset_functions = 'reset_' in test_content
                # Check for direct variable resets in tearDown (e.g., var_name = 0)
                if is_gtest:
                    teardown_section = re.search(r'void TearDown\(\)\s*(?:override)?\s*{([^}]*)}', test_content, re.DOTALL)
                else:
                    teardown_section = re.search(r'void tearDown\(void\)\s*{([^}]*)}', test_content, re.DOTALL)
                
                has_direct_resets = False
                if teardown_section:
                    teardown_content = teardown_section.group(1)
                    # Look for variable assignments to 0, 0.0f, NULL, etc.
                    has_direct_resets = bool(re.search(r'\w+\s*=\s*(0|0\.0f|NULL|false|"DEFAULT");', teardown_content))

                if not has_reset_functions and not has_direct_resets:
                    result['issues'].append("tearDown() function should reset stub variables (call counts and return values)")

        # Check for meaningful test content
        if len(test_names) == 0:
            if is_gtest:
                result['issues'].append("No Google Test macros found (TEST or TEST_F)")
            else:
                result['issues'].append("No test functions found (functions should start with 'test_')")

        # Check for test isolation (each test should be independent)
        if has_setup and has_teardown:
            # This is good - tests are properly isolated
            pass
        elif len(test_names) > 1:
            result['issues'].append("Multiple tests without setUp/tearDown - may not be properly isolated")

    def _verify_logical_consistency(self, test_content: str, result: Dict, is_gtest: bool = False):
        """Verify logical consistency"""

        # Check for contradictory assertions in the same test
        if is_gtest:
             test_sections = re.split(r'TEST(?:_F)?\s*\([^)]+\)\s*\{', test_content)[1:]
        else:
             test_sections = re.split(r'void test_\w+\s*\(', test_content)[1:]  # Split by test functions

        for i, section in enumerate(test_sections):
            test_name = f"test_{i+1}"  # Approximate name
            if is_gtest:
                assertions = re.findall(r'(?:EXPECT|ASSERT)_\w+\s*\([^)]+\)', section)
                true_asserts = [a for a in assertions if 'TRUE' in a]
                false_asserts = [a for a in assertions if 'FALSE' in a]
            else:
                assertions = re.findall(r'TEST_ASSERT_\w+\s*\([^)]+\)', section)
                true_asserts = [a for a in assertions if 'TEST_ASSERT_TRUE' in a]
                false_asserts = [a for a in assertions if 'TEST_ASSERT_FALSE' in a]

            if true_asserts and false_asserts:
                # Check if they're testing different variables
                if is_gtest:
                    true_vars = [re.search(r'(?:EXPECT|ASSERT)_TRUE\s*\(\s*([^)]+)', a) for a in true_asserts]
                    false_vars = [re.search(r'(?:EXPECT|ASSERT)_FALSE\s*\(\s*([^)]+)', a) for a in false_asserts]
                else:
                    true_vars = [re.search(r'TEST_ASSERT_TRUE\s*\(\s*([^)]+)', a) for a in true_asserts]
                    false_vars = [re.search(r'TEST_ASSERT_FALSE\s*\(\s*([^)]+)', a) for a in false_asserts]

                if true_vars and false_vars:
                    true_var_names = [match.group(1).strip() if match else "" for match in true_vars]
                    false_var_names = [match.group(1).strip() if match else "" for match in false_vars]

                    # If same variable has both TRUE and FALSE assertions, that's suspicious
                    common_vars = set(true_var_names) & set(false_var_names)
                    if common_vars:
                        result['issues'].append(f"Test {test_name}: contradictory assertions for variables {common_vars}")

        # Check for reasonable assertion values
        if is_gtest:
            equal_assertions = re.findall(r'(?:EXPECT|ASSERT)_EQ\s*\(\s*([^,]+)\s*,\s*([^)]+)\s*\)', test_content)
        else:
            equal_assertions = re.findall(r'TEST_ASSERT_EQUAL\s*\(\s*([^,]+)\s*,\s*([^)]+)\s*\)', test_content)
            
        for expected, actual in equal_assertions:
            # Check for obviously wrong assertions like TEST_ASSERT_EQUAL(1, 2)
            try:
                exp_val = int(expected.strip())
                act_val = int(actual.strip())
                if exp_val != act_val and abs(exp_val - act_val) > 1000:  # Large difference
                    result['issues'].append(f"Unreasonable assertion: ({exp_val}, {act_val})")
            except (ValueError, AttributeError):
                pass  # Not simple integers, skip

    def _check_embedded_features(self, test_content: str, source_content: str, result: Dict):
        """Check embedded hardware and safety-critical features"""
        
        # Check for volatile register handling
        if 'volatile' in source_content:
            if 'volatile' not in test_content:
                result['issues'].append("Source uses volatile registers but tests don't handle volatile semantics")
                result['compiles'] = False
        
        # Check for bit field testing
        bitfield_patterns = [r'\w+\s*:\s*\d+', r'unsigned\s+\w+\s*:\s*\d+']  # bit field declarations
        if any(re.search(pattern, source_content) for pattern in bitfield_patterns):
            # Check if tests use bit operations
            bit_operations = ['<<', '>>', '&', '|', '~', '^']
            has_bit_ops = any(op in test_content for op in bit_operations)
            if not has_bit_ops:
                result['issues'].append("Source uses bit fields but tests don't perform bit operations")
        
        # Check for state machine testing
        state_machine_indicators = ['state', 'STATE_', 'enum.*state', 'switch.*state']
        has_state_machine = any(re.search(pattern, source_content, re.IGNORECASE) for pattern in state_machine_indicators)
        if has_state_machine:
            # Check for state transition tests
            transition_tests = ['transition', 'state_change', 'next_state']
            has_transition_tests = any(indicator in test_content.lower() for indicator in transition_tests)
            if not has_transition_tests:
                result['issues'].append("Source has state machine but tests don't verify state transitions")
        
        # Check for TMR voting logic
        tmr_indicators = ['tmr', 'triple', 'voting', 'majority']
        has_tmr = any(indicator in source_content.lower() for indicator in tmr_indicators)
        if has_tmr:
            # Check for voting test scenarios
            voting_scenarios = ['aaa', 'aab', 'abc', 'fault', 'disagree']
            has_voting_tests = any(scenario in test_content.lower() for scenario in voting_scenarios)
            if not has_voting_tests:
                result['issues'].append("Source has TMR voting but tests don't cover voting scenarios")
        
        # Check for watchdog timer testing
        watchdog_indicators = ['watchdog', 'wdt', 'timeout', 'feed']
        has_watchdog = any(indicator in source_content.lower() for indicator in watchdog_indicators)
        if has_watchdog:
            # Check for timeout and feeding tests
            watchdog_tests = ['timeout', 'feed', 'reset_prevent']
            has_watchdog_tests = any(test in test_content.lower() for test in watchdog_tests)
            if not has_watchdog_tests:
                result['issues'].append("Source has watchdog timer but tests don't verify feeding/timeout")
        
        # Check for DMA/interrupt testing
        dma_indicators = ['dma', 'interrupt', 'irq', 'isr']
        has_dma = any(indicator in source_content.lower() for indicator in dma_indicators)
        if has_dma:
            # Check for hardware interaction simulation
            hardware_simulation = ['register', 'peripheral', 'mock', 'stub']
            has_hardware_tests = any(sim in test_content.lower() for sim in hardware_simulation)
            if not has_hardware_tests:
                result['issues'].append("Source uses DMA/interrupts but tests don't simulate hardware interactions")
        
        # Check for memory-mapped I/O
        mmio_indicators = ['mmio', 'memory.*map', 'register.*0x', 'volatile.*uint32_t']
        has_mmio = any(re.search(pattern, source_content, re.IGNORECASE) for pattern in mmio_indicators)
        if has_mmio:
            # Check for register access patterns
            register_access = ['=', '&=', '|=', '^=', 'read', 'write']
            has_register_tests = any(access in test_content for access in register_access)
            if not has_register_tests:
                result['issues'].append("Source uses memory-mapped I/O but tests don't verify register access")

    def _calculate_quality_rating(self, result: Dict) -> str:
        """Calculate overall quality rating"""
        issues = len(result['issues'])

        if issues == 0 and result['compiles'] and result['realistic']:
            return 'High'
        elif issues <= 2 and result['compiles']:
            return 'Medium'
        else:
            return 'Low'

    def _extract_test_functions(self, content: str) -> List[Dict]:
        """Extract test function definitions from test content"""
        functions = []
        
        # Match standard C function definitions
        func_pattern = r'(\w+)\s+(\w+)\s*\([^)]*\)\s*{'
        matches = re.findall(func_pattern, content)

        for return_type, func_name in matches:
            # Skip GTest macros which look like functions but aren't standard C functions
            if func_name in ['TEST', 'TEST_F', 'TEST_P']:
                continue
            functions.append({
                'name': func_name,
                'return_type': return_type
            })
            
        # Match GTest macros to extract test names
        gtest_pattern = r'(TEST(?:_F|_P)?)\s*\(\s*(\w+)\s*,\s*(\w+)\s*\)'
        gtest_matches = re.findall(gtest_pattern, content)
        
        for macro, suite, name in gtest_matches:
            functions.append({
                'name': f"{suite}_{name}", # Combine suite and name for unique ID
                'return_type': 'void' # Tests return void
            })

        return functions

    def print_validation_report(self, report: Dict):
        """Print a formatted validation report"""
        print(f"\n[REPORT] {report['file']}")
        print(f"   Quality: {report['quality']}")
        print(f"   Compiles: {'YES' if report['compiles'] else 'NO'}")
        print(f"   Realistic: {'YES' if report['realistic'] else 'NO'}")

        if report['issues']:
            print(f"   Issues ({len(report['issues'])}):")
            for issue in report['issues'][:5]:  # Show first 5 issues
                print(f"     - {issue}")
            if len(report['issues']) > 5:
                print(f"     ... and {len(report['issues']) - 5} more")

    def save_validation_report(self, report: Dict, report_dir: str):
        """Save validation report to file"""
        # Mirror source folder structure under compilation_report for easier browsing.
        # Example: source_rel=src/logic/Interlocking.cpp -> tests/compilation_report/src/logic/test_Interlocking_compiles_yes.txt
        dest_dir = report_dir
        source_rel = (report.get('source_rel') or '').replace('\\', '/')
        if source_rel and not source_rel.startswith('..'):
            try:
                parent = os.path.dirname(source_rel)
                if parent and parent != '.':
                    dest_dir = os.path.join(report_dir, parent)
            except Exception:
                dest_dir = report_dir

        os.makedirs(dest_dir, exist_ok=True)

        # Use the test filename stem so it stays stable even when source structure changes.
        base_name = os.path.splitext(os.path.basename(report.get('test_file') or report['file']))[0]
        compiles_status = "compiles_yes" if report['compiles'] else "compiles_no"
        filename = f"{base_name}_{compiles_status}.txt"
        filepath = os.path.join(dest_dir, filename)

        def _unique_path(path: str) -> str:
            root, ext = os.path.splitext(path)
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            candidate = f"{root}_{stamp}{ext}"
            i = 2
            while os.path.exists(candidate):
                candidate = f"{root}_{stamp}_{i}{ext}"
                i += 1
            return candidate

        if os.path.exists(filepath):
            overwrite = False
            if sys.stdin.isatty():
                ans = input(f"Validation report already exists: {os.path.basename(filepath)}. Replace? (y/n): ").strip().lower()
                overwrite = (ans == 'y')
            if not overwrite:
                filepath = _unique_path(filepath)

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"Validation Report for {report['file']}\n")
            f.write(f"Quality: {report['quality']}\n")
            f.write(f"Compiles: {report['compiles']}\n")
            f.write(f"Realistic: {report['realistic']}\n")
            f.write(f"Issues: {len(report['issues'])}\n")
            f.write("\nIssues:\n")
            for issue in report['issues']:
                f.write(f"- {issue}\n")

            if report['keep']:
                f.write("\nKeep:\n")
                for item in report['keep']:
                    f.write(f"- {item}\n")

            if report['fix']:
                f.write("\nFix:\n")
                for item in report['fix']:
                    f.write(f"- {item}\n")

            if report['remove']:
                f.write("\nRemove:\n")
                for item in report['remove']:
                    f.write(f"- {item}\n")

        return filepath