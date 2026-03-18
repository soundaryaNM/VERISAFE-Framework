"""
Test Intelligence Analyzer - Provides advanced analysis of test failures and fixes
"""

import os
import re
from typing import Dict, List, Tuple
from pathlib import Path
import json
import requests
from typing import Any
from .llm_config import get_model_for_role


class TestIntelligenceAnalyzer:
    """AI-powered test intelligence analyzer for root cause identification and fix guidance"""

    def __init__(self, api_key: str = None, model_choice: str = "gemini", ollama_url: str = "http://127.0.0.1:11434/api/generate", ollama_model: str = "qwen2.5-coder"):
        self.model_choice = model_choice
        self.ollama_url = ollama_url
        self.ollama_model = ollama_model
        # Enable streaming by default for local Ollama to show progress in terminal
        self.enable_streaming = True if self.model_choice == "ollama" else False
        
        if self.model_choice == "gemini":
            if not api_key:
                raise ValueError("API key required for Gemini model")
            try:
                import google.genai as genai  # type: ignore
            except ModuleNotFoundError as e:
                raise ModuleNotFoundError(
                    "Gemini support requires the 'google-genai' package. Install it via: pip install google-genai"
                ) from e

            self.client: Any = genai.Client(api_key=api_key)
            self.model_name = 'gemini-1.5-flash'  # Use a stable model name
        elif self.model_choice == "ollama":
            self.client = None
            pass # Connection check happens on first call or could be added here
        else:
            raise ValueError(f"Invalid model choice: {model_choice}")

    def _call_llm(self, prompt: str) -> str:
        """Call the selected LLM with the prompt"""
        if self.model_choice == "gemini":
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt
            )
            return response.text
        elif self.model_choice == "ollama":
            # Determine if self.ollama_model holds a logical role or a concrete model tag
            known_roles = {"planner", "synthesizer", "fix_it"}
            if self.ollama_model in known_roles:
                role = self.ollama_model
                model = get_model_for_role(role)
            else:
                role = "unknown"
                model = self.ollama_model

            print(f"[LLM] role={role} model={model}")
            # Prepare payload; support streaming if enabled
            payload = {
                "model": model,
                "prompt": prompt,
                "stream": bool(self.enable_streaming)
            }

            # Use a generous timeout for analysis as it can be complex
            try:
                if self.enable_streaming:
                    with requests.post(self.ollama_url, json=payload, stream=True, timeout=300) as resp:
                        resp.raise_for_status()
                        accumulated = []
                        # Iterate over streaming lines as they arrive
                        for raw in resp.iter_lines(decode_unicode=True):
                            if not raw:
                                continue
                            line = raw.strip()
                            # Try to parse JSON line if possible
                            try:
                                j = json.loads(line)
                                # Common Ollama stream shapes include fields like 'response' or 'chunk'
                                token = j.get('response') or j.get('chunk') or j.get('token') or None
                                if token is None:
                                    # If the JSON has a nested 'data' structure, try to stringify
                                    token = json.dumps(j)
                            except Exception:
                                token = line
                            # Print progress token to terminal
                            try:
                                print(token, end='', flush=True)
                            except Exception:
                                pass
                            accumulated.append(str(token))
                        # Ensure newline after streaming output
                        print()
                        full = ''.join(accumulated)
                        # Try to extract a 'response' from final JSON if present
                        try:
                            parsed = json.loads(full)
                            return parsed.get('response', full)
                        except Exception:
                            return full
                else:
                    response = requests.post(self.ollama_url, json=payload, timeout=300)
                    response.raise_for_status()
                    return response.json().get("response", response.text)
            except requests.exceptions.RequestException as e:
                raise
        else:
            raise ValueError(f"Unknown model choice: {self.model_choice}")

    def analyze_test_failures(self, test_results: Dict, source_files: List[str]) -> Dict:
        """
        Analyze test execution results and provide intelligence report

        Args:
            test_results: Dictionary containing test execution results
            source_files: List of source files being tested

        Returns:
            Intelligence report with root causes, fixes, and prioritization
        """
        intelligence_report = {
            'executive_summary': {},
            'priority_fixes': [],
            'detailed_analysis': [],
            'quality_metrics': {},
            'roi_analysis': {},
            'recommendations': []
        }

        # Analyze each test result
        for test_name, result in test_results.items():
            if not result.get('passed', True):
                analysis = self._analyze_single_failure(test_name, result, source_files)
                intelligence_report['detailed_analysis'].append(analysis)

        # Generate priority fixes
        intelligence_report['priority_fixes'] = self._prioritize_fixes(intelligence_report['detailed_analysis'])

        # Calculate quality metrics
        intelligence_report['quality_metrics'] = self._calculate_quality_metrics(test_results, intelligence_report['detailed_analysis'])

        # Generate executive summary
        intelligence_report['executive_summary'] = self._generate_executive_summary(intelligence_report)

        # Calculate ROI
        intelligence_report['roi_analysis'] = self._calculate_roi(intelligence_report)

        return intelligence_report

    def _analyze_single_failure(self, test_name: str, result: Dict, source_files: List[str]) -> Dict:
        """Analyze a single test failure and provide detailed intelligence"""

        # Extract error information
        error_output = result.get('error_output', '')
        compilation_errors = result.get('compilation_errors', [])

        # Use AI to analyze the failure
        analysis_prompt = f"""
        Analyze this C unit test failure and provide detailed intelligence:

        Test Name: {test_name}
        Error Output: {error_output}
        Compilation Errors: {json.dumps(compilation_errors, indent=2)}

        Source Files: {', '.join(source_files)}

        Provide analysis in this exact JSON format:
        {{
            "root_cause": "Brief description of why the test failed",
            "error_category": "COMPILATION|LOGIC|RUNTIME|DEPENDENCY",
            "severity": "CRITICAL|HIGH|MEDIUM|LOW",
            "fix_complexity": "EASY|MEDIUM|HARD",
            "estimated_fix_time": "X minutes",
            "fix_instructions": ["Step 1", "Step 2", "Step 3"],
            "impact_assessment": "What this fix achieves",
            "code_changes_required": "Brief description of changes needed",
            "prerequisites": ["Any prerequisites for the fix"],
            "alternative_solutions": ["Alternative approaches if primary fix fails"]
        }}
        """

        try:
            response_text = self._call_llm(analysis_prompt)
            analysis = json.loads(response_text.strip('```json\n').strip('```'))
        except Exception as e:
            # Fallback analysis if AI fails
            print(f"[WARN] AI Analysis failed: {e}")
            analysis = self._fallback_analysis(test_name, result)

        analysis['test_name'] = test_name
        analysis['confidence_score'] = self._calculate_confidence_score(analysis)

        return analysis

    def _fallback_analysis(self, test_name: str, result: Dict) -> Dict:
        """Provide basic analysis when AI analysis fails"""
        error_output = result.get('error_output', '').lower()

        analysis = {
            "root_cause": "Unable to determine automatically - manual review required",
            "error_category": "UNKNOWN",
            "severity": "MEDIUM",
            "fix_complexity": "MEDIUM",
            "estimated_fix_time": "15 minutes",
            "fix_instructions": ["Review error output manually", "Check compilation errors", "Verify test logic"],
            "impact_assessment": "Manual debugging required",
            "code_changes_required": "TBD - requires manual analysis",
            "prerequisites": [],
            "alternative_solutions": ["Consult C programming documentation", "Review similar test patterns"]
        }

        # Basic pattern matching for common errors
        if 'undefined reference' in error_output:
            analysis.update({
                "root_cause": "Missing function definition or incorrect linking",
                "error_category": "DEPENDENCY",
                "fix_complexity": "MEDIUM"
            })
        elif 'expected' in error_output and 'but was' in error_output:
            analysis.update({
                "root_cause": "Test assertion failure - actual vs expected values don't match",
                "error_category": "LOGIC",
                "fix_complexity": "EASY"
            })
        elif 'void value not ignored' in error_output:
            analysis.update({
                "root_cause": "Attempting to assign void function result",
                "error_category": "COMPILATION",
                "fix_complexity": "EASY"
            })

        return analysis

    def _prioritize_fixes(self, detailed_analysis: List[Dict]) -> List[Dict]:
        """Prioritize fixes based on complexity, impact, and severity"""

        # Calculate priority score for each fix
        for analysis in detailed_analysis:
            priority_score = self._calculate_priority_score(analysis)
            analysis['priority_score'] = priority_score

        # Sort by priority score (higher is better)
        sorted_analysis = sorted(detailed_analysis, key=lambda x: x['priority_score'], reverse=True)

        # Group by complexity
        priority_fixes = []
        for analysis in sorted_analysis:
            priority_fixes.append({
                'test_name': analysis['test_name'],
                'complexity': analysis['fix_complexity'],
                'estimated_time': analysis['estimated_fix_time'],
                'impact': analysis['impact_assessment'],
                'root_cause': analysis['root_cause'],
                'fix_instructions': analysis['fix_instructions'][:3],  # Top 3 steps
                'priority_score': analysis['priority_score']
            })

        return priority_fixes

    def _calculate_priority_score(self, analysis: Dict) -> float:
        """Calculate priority score based on multiple factors"""

        # Base scores
        severity_scores = {'CRITICAL': 100, 'HIGH': 75, 'MEDIUM': 50, 'LOW': 25}
        complexity_scores = {'EASY': 80, 'MEDIUM': 50, 'HARD': 20}

        severity_score = severity_scores.get(analysis.get('severity', 'MEDIUM'), 50)
        complexity_score = complexity_scores.get(analysis.get('complexity', 'MEDIUM'), 50)

        # Extract time estimate
        time_match = re.search(r'(\d+)', analysis.get('estimated_fix_time', '15'))
        time_estimate = int(time_match.group(1)) if time_match else 15

        # Time bonus (faster fixes get higher priority)
        time_bonus = max(0, 20 - time_estimate)  # Max 20 points for very quick fixes

        # Confidence bonus
        confidence_bonus = analysis.get('confidence_score', 50) * 0.2

        total_score = severity_score + complexity_score + time_bonus + confidence_bonus

        return round(total_score, 1)

    def _calculate_quality_metrics(self, test_results: Dict, detailed_analysis: List[Dict]) -> Dict:
        """Calculate overall quality metrics for the test suite"""

        total_tests = len(test_results)
        passed_tests = sum(1 for result in test_results.values() if result.get('passed', False))
        failed_tests = total_tests - passed_tests

        # Calculate pass rate (0-100)
        pass_rate = (passed_tests / total_tests) if total_tests > 0 else 0
        
        # Calculate quality score with transparent formula
        # Formula: (pass_rate × 40%) + ((1 - failure_rate) × 30%) + (maintainability × 30%)
        # Where maintainability is inversely proportional to fix complexity
        
        # Penalty for complexity of failures (affects maintainability score)
        complexity_penalty = 0
        for analysis in detailed_analysis:
            if analysis.get('fix_complexity') == 'HARD':
                complexity_penalty += 10
            elif analysis.get('fix_complexity') == 'MEDIUM':
                complexity_penalty += 5
            elif analysis.get('fix_complexity') == 'EASY':
                complexity_penalty += 2

        # Maintainability score (0-100): higher complexity = lower maintainability
        max_possible_penalty = len(detailed_analysis) * 10 if detailed_analysis else 1
        maintainability_score = max(0, 100 - (complexity_penalty / max_possible_penalty * 100))
        
        # Calculate weighted quality score
        quality_score = (pass_rate * 40) + ((1 - (failed_tests / total_tests if total_tests > 0 else 0)) * 30) + (maintainability_score * 0.3)

        # Estimate coverage potential based on current quality and test improvements
        # Current pass rate + potential improvement from fixing issues
        coverage_potential = min(95, (pass_rate * 100) + (len(detailed_analysis) * 2))

        # Calculate maintenance complexity based on fix complexity distribution
        maintenance_complexity = 'LOW'
        hard_fixes = sum(1 for a in detailed_analysis if a.get('fix_complexity') == 'HARD')
        medium_fixes = sum(1 for a in detailed_analysis if a.get('fix_complexity') == 'MEDIUM')
        
        if hard_fixes > 0 or complexity_penalty > 20:
            maintenance_complexity = 'HIGH'
        elif medium_fixes > 2 or complexity_penalty > 10:
            maintenance_complexity = 'MEDIUM'

        return {
            'quality_score': round(quality_score, 1),
            'coverage_potential': round(coverage_potential, 1),
            'maintenance_complexity': maintenance_complexity,
            'total_tests': total_tests,
            'passed_tests': passed_tests,
            'failed_tests': failed_tests,
            'failure_rate': round((failed_tests / total_tests) * 100, 1) if total_tests > 0 else 0,
            'pass_rate': round(pass_rate * 100, 1),
            'maintainability_score': round(maintainability_score, 1)
        }

    def _generate_executive_summary(self, intelligence_report: Dict) -> Dict:
        """Generate executive summary with key insights"""

        quality_metrics = intelligence_report['quality_metrics']
        priority_fixes = intelligence_report['priority_fixes']

        # Calculate time savings with transparent formula
        # Manual debugging time: 30 minutes per failure (industry average without AI guidance)
        # AI-guided fix time: Sum of estimated fix times from intelligence analysis
        
        manual_debugging_time_per_failure = 30
        total_manual_time = quality_metrics['failed_tests'] * manual_debugging_time_per_failure
        
        # AI-guided time: sum of top priority fixes
        ai_guided_time = 0
        for fix in priority_fixes[:10]:  # Consider top 10 fixes
            time_match = re.search(r'(\d+)', fix.get('estimated_time', '15 minutes'))
            if time_match:
                ai_guided_time += int(time_match.group(1))
        
        time_savings = total_manual_time - ai_guided_time

        # Determine recommended fix order based on complexity distribution
        complexity_order = []
        for fix in priority_fixes[:5]:
            complexity = fix.get('complexity', 'MEDIUM')
            if complexity not in complexity_order:
                complexity_order.append(complexity)
        
        recommended_order = ' → '.join(complexity_order) if complexity_order else 'N/A'

        return {
            'total_time_savings': f"{time_savings} minutes",
            'recommended_fix_order': recommended_order,
            'expected_final_coverage': quality_metrics['coverage_potential'],
            'quality_improvement_potential': f"{quality_metrics['coverage_potential'] - quality_metrics['quality_score']:.1f} points",
            'top_priority_fixes': len([f for f in priority_fixes if f.get('complexity') == 'EASY']),
            'manual_debugging_time': f"{total_manual_time} minutes",
            'ai_guided_fix_time': f"{ai_guided_time} minutes"
        }

    def _calculate_roi(self, intelligence_report: Dict) -> Dict:
        """Calculate return on investment metrics with transparent formulas"""

        exec_summary = intelligence_report['executive_summary']
        quality_metrics = intelligence_report['quality_metrics']

        # Extract time savings in minutes
        time_savings_match = re.search(r'(-?\d+)', exec_summary['total_time_savings'])
        time_savings_minutes = int(time_savings_match.group(1)) if time_savings_match else 0
        
        # Convert to hours for dollar calculation
        time_savings_hours = time_savings_minutes / 60
        
        # Engineering cost calculation
        # Assumption: $50/hour average engineering rate
        hourly_rate = 50
        dollar_savings = time_savings_hours * hourly_rate

        # Quality improvement ROI calculation
        # Formula: Quality points improved × $10 per point
        # Rationale: Each quality point represents reduced technical debt and bug prevention
        quality_improvement = float(exec_summary['quality_improvement_potential'].split()[0])
        quality_roi = quality_improvement * 10

        # Debugging efficiency calculation
        # Formula: (Manual time - AI-guided time) / Manual time × 100
        manual_time_match = re.search(r'(\d+)', exec_summary.get('manual_debugging_time', '0 minutes'))
        ai_time_match = re.search(r'(\d+)', exec_summary.get('ai_guided_fix_time', '0 minutes'))
        
        manual_time = int(manual_time_match.group(1)) if manual_time_match else 1
        ai_time = int(ai_time_match.group(1)) if ai_time_match else 0
        
        debugging_efficiency = ((manual_time - ai_time) / manual_time * 100) if manual_time > 0 else 0

        return {
            'engineering_time_saved': f"${dollar_savings:.0f}",
            'quality_improvement_value': f"${quality_roi:.0f}",
            'total_roi': f"${dollar_savings + quality_roi:.0f}",
            'debugging_efficiency': f"{debugging_efficiency:.1f}% faster debugging",
            'calculation_basis': {
                'hourly_rate': f"${hourly_rate}/hour",
                'time_saved_hours': f"{time_savings_hours:.2f} hours",
                'quality_value_per_point': '$10/point'
            }
        }

    def _calculate_confidence_score(self, analysis: Dict) -> int:
        """Calculate confidence score for the analysis (0-100)"""

        confidence = 50  # Base confidence

        # Boost confidence based on analysis quality
        if analysis.get('root_cause') and 'Unable to determine' not in analysis['root_cause']:
            confidence += 20

        if analysis.get('fix_instructions') and len(analysis['fix_instructions']) > 0:
            confidence += 15

        if analysis.get('error_category') != 'UNKNOWN':
            confidence += 10

        if analysis.get('estimated_fix_time') and 'TBD' not in analysis['estimated_fix_time']:
            confidence += 5

        return min(100, confidence)

    def generate_intelligence_report(self, output_path: str, intelligence_report: Dict):
        """Generate a comprehensive markdown intelligence report"""

        exec_summary = intelligence_report['executive_summary']
        quality_metrics = intelligence_report['quality_metrics']
        roi_analysis = intelligence_report['roi_analysis']
        
        # Calculate total estimated fix time for priority fixes
        priority_fixes = intelligence_report['priority_fixes']
        total_fix_time = 0
        for fix in priority_fixes[:10]:
            time_match = re.search(r'(\d+)', fix.get('estimated_time', '0'))
            if time_match:
                total_fix_time += int(time_match.group(1))

        report_content = f"""# Test Generation Intelligence Report

## Executive Summary
- **Total time savings vs. manual**: {exec_summary['total_time_savings']} (Manual: {exec_summary.get('manual_debugging_time', 'N/A')} vs AI-guided: {exec_summary.get('ai_guided_fix_time', 'N/A')})
- **Recommended fix order**: {exec_summary['recommended_fix_order']}
- **Expected final coverage**: {exec_summary['expected_final_coverage']}%
- **Test Results**: {quality_metrics['passed_tests']}/{quality_metrics['total_tests']} passed ({quality_metrics['pass_rate']}%)

## Quality Metrics
- **Code Quality Score**: {quality_metrics['quality_score']}/100
  - Formula: (Pass Rate × 40%) + (Non-Failure Rate × 30%) + (Maintainability × 30%)
  - Pass Rate: {quality_metrics['pass_rate']}% | Failure Rate: {quality_metrics['failure_rate']}% | Maintainability: {quality_metrics['maintainability_score']}/100
- **Test Coverage Potential**: {quality_metrics['coverage_potential']}%
- **Maintenance Complexity**: {quality_metrics['maintenance_complexity']}
- **Failed Tests**: {quality_metrics['failed_tests']}/{quality_metrics['total_tests']} ({quality_metrics['failure_rate']}%)

## ROI Analysis
- **Engineering time saved**: {roi_analysis['engineering_time_saved']} (based on {roi_analysis['calculation_basis']['hourly_rate']} × {roi_analysis['calculation_basis']['time_saved_hours']})
- **Quality improvement value**: {roi_analysis['quality_improvement_value']} (at {roi_analysis['calculation_basis']['quality_value_per_point']})
- **Total ROI**: {roi_analysis['total_roi']}
- **Debugging efficiency**: {roi_analysis['debugging_efficiency']}

## Priority Fixes (Estimated {total_fix_time} minutes total)

"""

        # Add priority fixes
        easy_fixes = [f for f in intelligence_report['priority_fixes'] if f.get('complexity') == 'EASY']
        for i, fix in enumerate(easy_fixes[:5], 1):
            report_content += f"""### {i}. {fix['test_name']} - {fix['estimated_time']}
**Root Cause**: {fix['root_cause']}
**Impact**: {fix['impact']}
**Fix Steps**:
"""
            for j, step in enumerate(fix['fix_instructions'], 1):
                report_content += f"  {j}. {step}\n"
            report_content += "\n"

        # Add detailed analysis
        report_content += "## Detailed Analysis\n\n"
        for analysis in intelligence_report['detailed_analysis']:
            report_content += f"""### {analysis['test_name']}
- **Root Cause**: {analysis['root_cause']}
- **Category**: {analysis['error_category']}
- **Severity**: {analysis['severity']}
- **Complexity**: {analysis['fix_complexity']}
- **Estimated Time**: {analysis['estimated_fix_time']}
- **Confidence**: {analysis['confidence_score']}%

**Fix Instructions**:
"""
            for step in analysis['fix_instructions']:
                report_content += f"- {step}\n"

            report_content += f"""
**Impact**: {analysis['impact_assessment']}
**Code Changes**: {analysis['code_changes_required']}

**Prerequisites**:
"""
            for prereq in analysis.get('prerequisites', []):
                report_content += f"- {prereq}\n"

            report_content += "\n**Alternative Solutions**:\n"
            for alt in analysis.get('alternative_solutions', []):
                report_content += f"- {alt}\n"
            report_content += "\n---\n\n"

        # Write report
        with open(output_path, 'w') as f:
            f.write(report_content)

    def generate_fix_priority_csv(self, output_path: str, intelligence_report: Dict):
        """Generate CSV file with fix priorities"""

        import csv

        with open(output_path, 'w', newline='') as csvfile:
            fieldnames = ['test_name', 'complexity', 'estimated_time', 'priority_score', 'root_cause', 'impact']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

            writer.writeheader()
            for fix in intelligence_report['priority_fixes']:
                writer.writerow({
                    'test_name': fix['test_name'],
                    'complexity': fix['complexity'],
                    'estimated_time': fix['estimated_time'],
                    'priority_score': fix['priority_score'],
                    'root_cause': fix['root_cause'],
                    'impact': fix['impact']
                })