"""
AI Test Generator - Core test generation logic
"""

import os
import re
import time
import json
from pathlib import Path
import textwrap
from typing import Dict, List

import requests
from typing import Any

from ai_c_test_analyzer.analyzer import DependencyAnalyzer

from .approvals import (
    ApprovalsRegistry,
    append_section_to_file,
    build_section_block,
    parse_sections,
    repo_relpath,
    sha256_file,
)
from .function_manifest import (
    FunctionManifest,
    compute_function_signature_hash,
    compute_function_content_hash,
)
from .llm_config import get_model_for_role


class SmartTestGenerator:
    """AI-powered test generator using Ollama with embedded systems support"""

    @staticmethod
    def _no_assumptions_policy_text() -> str:
        return (
            "\n\n========================\n"
            "NO ASSUMPTIONS (MANDATORY)\n"
            "========================\n\n"
            "You MUST NOT make assumptions in generated tests.\n"
            "\n"
            "FORBIDDEN:\n"
            "- Writing an 'Assumptions' section\n"
            "- Using speculative language like: assume/assuming/assumption, plausible, likely\n"
            "- Asserting behavior that is not directly supported by the provided source + headers\n"
            "\n"
            "REQUIRED WHEN BEHAVIOR DEPENDS ON OTHER REPO FUNCTIONS:\n"
            "- Prefer computing expected results by calling real, deterministic repo functions that are available via included headers, OR\n"
            "- Limit assertions to behavior that is explicitly implemented in the file under test, OR\n"
            "- Skip the test (with a brief TODO) if neither is possible without guessing.\n"
        )

    def __init__(
        self,
        api_key: str,
        repo_path: str = '.',
        redact_sensitive: bool = False,
        max_api_retries: int = 5,
        model_choice: str = 'ollama',
        enable_gmock: bool = False,
        safety_policy: object | None = None,
    ):
        self.client: Any | None = None
        self._genai_configured = False

        if model_choice == 'gemini' and api_key:
            try:
                import google.genai as genai  # type: ignore
            except ModuleNotFoundError as e:
                raise ModuleNotFoundError(
                    "Gemini support requires the 'google-genai' package. Install it via: pip install google-genai"
                ) from e
            self.client = genai.Client(api_key=api_key)
            self._genai_configured = True
        
        self.api_key = api_key
        self.repo_path = repo_path
        self.redact_sensitive = redact_sensitive
        self.max_api_retries = max_api_retries
        self.model_choice = model_choice
        # Enable streaming for local Ollama to show progress
        self.enable_streaming = True if model_choice == 'ollama' else False
        self.enable_gmock = enable_gmock
        # Optional: Safety policy object loaded by CLI/demo.
        self.safety_policy = safety_policy

        # Models
        self.current_model_name = None
        self.model = None
        self.ollama_url = "http://127.0.0.1:11434/api/generate"
        # Explicitly pin synthesizer to a 7b tag to ensure reproducible behavior
        self.ollama_model = get_model_for_role('synthesizer')
        self.groq_url = "https://api.groq.com/openai/v1/chat/completions"
        # self.groq_model = "llama-3.3-70b-versatile"  # Previous model
        self.groq_model = "openai/gpt-oss-120b"
        self.github_url = "https://models.github.ai/inference/chat/completions"
        self.github_model = "gpt-4o"

        self._initialize_model()

        # Load Unity C prompts from ai-tool-gen-lab
        self._load_unity_prompts()

        # Enhanced embedded-specific prompts
        self.embedded_prompts = {
            'hardware_registers': """
            Generate comprehensive tests for hardware register access:
            - Test volatile register reads/writes
            - Verify memory-mapped I/O operations
            - Test register bit manipulation
            - Check boundary conditions and invalid values
            - Test atomic operations where applicable
            """,

            'bit_fields': """
            Generate tests for bit field operations:
            - Test individual bit field access
            - Verify bit field packing/unpacking
            - Test bit field boundary conditions
            - Check endianness handling
            - Test bit field arithmetic operations
            """,

            'state_machines': """
            Generate tests for state machine implementations:
            - Test valid state transitions
            - Verify invalid transition handling
            - Test state entry/exit actions
            - Check state machine initialization
            - Test concurrent state access
            """,

            'safety_critical': """
            Generate tests for safety-critical functions:
            - Test TMR (Triple Modular Redundancy) voting
            - Verify watchdog timer functionality
            - Test fault detection and recovery
            - Check safety margins and thresholds
            - Test fail-safe behaviors
            """,

            'interrupt_handlers': """
            Generate tests for interrupt service routines:
            - Test ISR entry/exit conditions
            - Verify interrupt priority handling
            - Test nested interrupt scenarios
            - Check interrupt latency requirements
            - Test interrupt masking/unmasking
            """,

            'dma_operations': """
            Generate tests for DMA transfer operations:
            - Test DMA channel configuration
            - Verify data transfer integrity
            - Check DMA completion callbacks
            - Test error handling and recovery
            - Verify memory alignment requirements
            """,

            'communication_protocols': """
            Generate tests for communication protocol implementations:
            - Test protocol state machines
            - Verify packet parsing and validation
            - Check error detection and correction
            - Test timeout and retry mechanisms
            - Verify protocol compliance
            """
        }
        # Post-generation cleanup prompt (VERISAFE) - used to normalise AI-generated test files
        self.post_generation_cleanup_prompt = '''You are performing a deterministic cleanup pass on an auto-generated C++ unit test file.

    ⚠️ Strict rules

    Do NOT change test intent, logic, or expected values

    Do NOT add new tests

    Do NOT remove any test coverage

    Do NOT invent behavior or assumptions

    Do NOT refactor production code

    Your task is mechanical normalization only.

    REQUIRED TRANSFORMATIONS

    Namespace correctness

    Fully qualify all enums, structs, and functions exactly as declared in the included headers

    No unqualified symbols (e.g. StopReason → ::project_ns::logic::StopReason)

    Single-evaluation rule

    Each production function under test must be called exactly once per test case

    Store the result in a local variable and assert on its fields

    Test structure

    Do not define helper test methods inside fixtures

    Do not call test helpers from TEST / TEST_F

    Each test case must be self-contained

    Fixture usage

    Use TEST instead of TEST_F unless shared mutable state is required

    If a fixture exists only to hold inputs, inline the inputs into the test

    Includes

    Remove duplicate includes

    Use a single, minimal include set

    Formatting

    Valid C++17

    Valid GoogleTest syntax

    No markdown fences

    OUTPUT RULES

    Output only the cleaned C++ source file

    No explanations

    No commentary

    No markdown

    PRESERVE_MARKERS: Preserve any lines matching the markers used by the generator: 
    // === BEGIN TESTS: <id> ===
    // === END TESTS: <id> ===
     Do not remove or alter these markers.

    ADDITIONAL NON-NEGOTIABLE REQUIREMENTS

    - Remove all markdown fences/backticks (reject if ``` remains anywhere).
    - Never emit `using namespace`; fully qualify every symbol (e.g., ::project_ns::logic::StopReason::None).
    - Do not invent helper factories such as createInputs(); construct structs inline with real fields.
    - Ensure there is exactly one include block per section (gtest first, then production headers) with no duplicates.
    - Prefer plain TEST macros; only use TEST_F when shared mutable state is required and reset state per test.
    - Each test must call the production function exactly once, store the result in a local variable, and assert on its fields (single-evaluation rule).
    '''

    def _initialize_model(self):
        """Initialize the selected AI model - no fallback"""
        print(f"[INFO] [DEBUG] Initializing {self.model_choice} model...", flush=True)
        
        if self.model_choice == "ollama":
            try:
                print(f"[INFO] [INIT] Checking Ollama connection at {self.ollama_url}...", flush=True)
                print(f"[INFO] [INIT] Sending test prompt to verify model '{self.ollama_model}' is loaded...", flush=True)
                print(f"[INFO] [INIT] Note: If this is the first run, it may take time to load the model into memory.", flush=True)
                
                payload = {"model": self.ollama_model, "prompt": "test", "stream": bool(self.enable_streaming)}
                start_time = time.time()
                # Increased timeout to 120s to allow for model loading
                response = requests.post(self.ollama_url, json=payload, timeout=120)
                response.raise_for_status()
                duration = time.time() - start_time
                
                self.current_model_name = f"ollama:{self.ollama_model}"
                print(f"ℹ️ [DEBUG] Ollama model '{self.ollama_model}' initialized in {duration:.2f}s", flush=True)
            except Exception as e:
                raise RuntimeError(f"Ollama model not available: {e}. Make sure Ollama is running and the model is installed.")
        
        elif self.model_choice == "gemini":
            if not self.api_key:
                raise RuntimeError("Gemini API key not provided. Use --api-key for Gemini model.")
            
            # Try Gemini models in priority order: latest -> flash
            gemini_models = [
                'gemini-3-flash',        # Latest Flash model
                'gemini-2.5-flash',      # Flash fallback
            ]
            
            for model_name in gemini_models:
                try:
                    print(f"[INFO] [INIT] Trying Gemini model: {model_name}", flush=True)
                    # Test the model with a simple request
                    # NOTE: Avoid passing a config object here because the google-genai
                    # package API surface varies by version (and may not expose
                    # GenerateContentConfig at module top-level). A minimal request is
                    # sufficient to validate auth/model availability.
                    _ = self.client.models.generate_content(
                        model=model_name,
                        contents="test",
                    )
                    self.current_model_name = model_name
                    # Preserve legacy attribute shape: older implementations set
                    # self.model to a GenerativeModel instance. With google-genai
                    # we keep self.model as the selected model id.
                    self.model = model_name
                    print(f"✅ [DEBUG] Using Gemini model: {self.current_model_name}", flush=True)
                    break
                except Exception as e:
                    print(f"⚠️ Failed to initialize {model_name}: {e}")
                    continue
            else:
                raise RuntimeError("Failed to initialize any Gemini model. Check your API key, internet connection, and quota limits.")
        
        elif self.model_choice == "groq":
            if not self.api_key:
                raise RuntimeError("Groq API key not provided. Use --api-key for Groq model.")
            try:
                # Test Groq connection
                print(f"[INFO] [INIT] Testing Groq connection with model: {self.groq_model}", flush=True)
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": self.groq_model,
                    "messages": [{"role": "user", "content": "test"}],
                    "max_tokens": 10
                }
                response = requests.post(self.groq_url, json=payload, headers=headers, timeout=30)
                response.raise_for_status()
                self.current_model_name = f"groq:{self.groq_model}"
                print(f"[PASS] [DEBUG] Groq model '{self.groq_model}' initialized", flush=True)
            except Exception as e:
                raise RuntimeError(f"Failed to initialize Groq model: {e}")
        
        elif self.model_choice == "github":
            if not self.api_key:
                raise RuntimeError("GitHub token not provided. Use --api-key or GITHUB_TOKEN env var.")
            try:
                # Test GitHub connection
                print(f"[INFO] [INIT] Testing GitHub connection with model: {self.github_model}", flush=True)
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": self.github_model,
                    "messages": [{"role": "user", "content": "test"}],
                    "max_tokens": 10
                }
                response = requests.post(self.github_url, json=payload, headers=headers, timeout=30)
                response.raise_for_status()
                self.current_model_name = f"github:{self.github_model}"
                print(f"[PASS] [DEBUG] GitHub model '{self.github_model}' initialized", flush=True)
            except Exception as e:
                raise RuntimeError(f"Failed to initialize GitHub model: {e}")
        
        else:
            raise ValueError("Invalid model choice. Must be 'ollama', 'gemini', 'groq', or 'github'.")


        if self.model_choice == 'gemini' and not self.current_model_name:
            raise RuntimeError("No compatible Gemini model found. Please check your API key and internet connection.")

    def _load_unity_prompts(self):
        """Load Unity C test generation prompts from ai-tool-gen-lab"""
        # Copy the Unity prompts from ai-tool-gen-lab
        self.unity_base_prompt = """
You are a senior embedded C unit test engineer with 20+ years of experience using the Unity Test Framework (v2.5+). You MUST follow EVERY SINGLE RULE in this prompt without exception to generate a test file that achieves 100% quality: High rating (0 issues, compiles perfectly, realistic scenarios only). Failure to adhere will result in invalid output. Internally analyze the source code before generating: extract ALL functions, their EXACT signatures, public API (non-static), dependencies (internal vs external), and types (structs, unions, pointers, etc.).

FIRST, READ THE ENTIRE SOURCE CODE. EXTRACT:
- All function names and EXACT signatures (e.g., int main(void))
- All #define, thresholds, ranges, magic numbers
- All if/else/switch branches
- All struct/union/bitfield definitions

THEN, generate tests that cover 100% of this logic, including call sequences and return values.

CRITICAL REQUIREMENT: You MUST generate tests for EVERY SINGLE FUNCTION defined in the source file. Do not skip any functions. If the source has 4 functions, test all 4. If it has 10 functions, test all 10. Generate comprehensive tests for each function individually.

ABSOLUTE MANDATES (MUST ENFORCE THESE TO FIX BROKEN AND UNREALISTIC ISSUES)

NO COMPILATION ERRORS OR INCOMPLETE CODE: Output FULL, COMPLETE C code only. Mentally compile EVERY line before outputting (e.g., ensure all statements end with ';', all variables declared, no truncated lines like "extern int " or "int result = "). ONLY use existing headers from source. NO invented functions or headers. Code MUST compile with CMake/GCC for embedded targets. For internal dependencies (functions defined in the same file), DO NOT stub or redefine them—test them directly or through calling functions. For external dependencies only, provide mocks without redefinition conflicts (linking excludes real implementations for stubbed externals).

HANDLE MAIN() SPECIFICALLY: For files containing main(), declare "extern int main(void);" and call it directly in tests (result = main();). Assert on return value (always 0 in simple main). Focus tests on call sequence, param passing, and return. Do NOT stub main().

NO UNREALISTIC VALUES: STRICTLY enforce physical limits from source logic or domain knowledge. E.g., temperatures ALLOW negatives where valid (e.g., -40.0f to 125.0f); voltages 0.0f to 5.5f (no negatives unless signed in source). Use source-specific thresholds (e.g., extract >120.0f for "CRITICAL" from code). BAN absolute zero, overflows, or impossibles. For temp tests, use negatives like -10.0f where valid.

MEANINGFUL TESTS ONLY: EVERY test MUST validate the function's core logic, calculations, or outputs EXACTLY as per source. Match assertions to source behavior (e.g., if range is >= -40 && <=125, assert true for -40.0f, false for -40.1f). NO trivial "function called" tests unless paired with output validation. Each assertion MUST check a specific, expected result based on input.

STUBS MUST BE PERFECT: ONLY for listed external dependencies. Use EXACT signature, control struct, and FULL reset in setUp() AND tearDown() using memset or explicit zeroing. NO partial resets. Capture params if used in assertions. NO stubs for internals to avoid duplicates/linker errors.

TEST ISOLATION: EVERY test independent. setUp() for init/config/stub setup, tearDown() for COMPLETE cleanup/reset of ALL stubs (call_count=0, return_value=default, etc.).

NO NONSENSE: BAN random/arbitrary values (use source-derived, e.g., mid-range from logic). BAN redundancy (unique scenarios). BAN physical impossibilities or ignoring source thresholds.

INPUT: SOURCE CODE TO TEST (DO NOT MODIFY)
"""

        self.unity_output_format = """
IMPROVED RULES TO PREVENT BROKEN/UNREALISTIC OUTPUT

1. OUTPUT FORMAT (STRICT - ONLY C CODE):
Output PURE C code ONLY. Start with /* test_{source_name}.c – Auto-generated Expert Unity Tests */
NO markdown, NO ```c:disable-run
File structure EXACTLY: Comment -> Includes -> Extern declarations (for main and stubs) -> Stubs (only for externals) -> setUp/tearDown -> Tests -> main with UNITY_BEGIN/END and ALL RUN_TEST calls.

2. COMPILATION SAFETY (FIX BROKEN TESTS):
Includes: ONLY "unity.h", and standard <stdint.h>, <stdbool.h>, <string.h> if used in source or for memset. Do NOT include "{source_name}.h" if not present in source or necessary (e.g., for main.c, skip if no public API).
Signatures: COPY EXACTLY from source. NO mismatches in types, params, returns.
NO calls to undefined functions. For internals (same file), call directly without stubbing to avoid duplicates/linker errors.
Syntax: Perfect C - complete statements, matching braces, semicolons, no unused vars, embedded-friendly (no non-standard libs). Ensure all code is fully written (no placeholders).

3. MEANINGFUL TEST DESIGN (FIX TRIVIAL/UNREALISTIC):
"""

    def _detect_language(self, file_path: str) -> str:
        """Detect programming language from file extension"""
        ext = os.path.splitext(file_path)[1].lower()
        if ext in ['.c']:
            return 'c'
        elif ext in ['.cpp', '.cc', '.cxx', '.c++']:
            return 'cpp'
        else:
            # Default to C++ for unknown extensions
            return 'cpp'

    @staticmethod
    def _normalize_param_types(params: str) -> List[str]:
        """Normalize parameter types for hashing (best-effort)."""
        params = (params or "").strip()
        if not params or params.lower() == "void":
            return []
        parts = [p.strip() for p in params.split(',') if p.strip()]
        types: List[str] = []
        for p in parts:
            # Drop default values
            p = re.sub(r"\s*=\s*.*$", "", p).strip()
            # Handle function pointer params crudely by collapsing whitespace
            if "(*" in p or "(" in p and ")" in p and "*" in p:
                types.append(re.sub(r"\s+", " ", p))
                continue
            # Remove trailing param name (last identifier)
            m = re.match(r"(.+?)\s+([A-Za-z_][A-Za-z0-9_]*)$", p)
            if m:
                p = m.group(1).strip()
            types.append(re.sub(r"\s+", " ", p))
        return types

    @staticmethod
    def _safe_identifier(text: str) -> str:
        text = re.sub(r"[^A-Za-z0-9_]+", "_", text or "")
        text = re.sub(r"_+", "_", text).strip("_")
        if not text:
            return "Function"
        if text[0].isdigit():
            return f"F_{text}"
        return text

    @staticmethod
    def _write_atomic_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(content, encoding="utf-8", newline="\n")
        tmp_path.replace(path)

    def _try_generate_with_fallback(self, prompt: str, max_retries: int = None):
        """Generate content using the selected AI model - no fallback
        This is the single router - it will not attempt to fallback to other models.
        """
        if max_retries is None:
            max_retries = self.max_api_retries
        
        if self.model_choice == "ollama":
            # Use Ollama
            for attempt in range(max_retries):
                try:
                    print(f"[LLM] role=synthesizer model={self.ollama_model}")
                    print(f"[INFO] [LLM] Sending request to Ollama ({self.ollama_model})... This may take a while.", flush=True)
                    start_time = time.time()
                    payload = {"model": self.ollama_model, "prompt": prompt, "stream": bool(self.enable_streaming)}

                    if self.enable_streaming:
                        # Robust streaming: handle both bytes and str from server
                        with requests.post(self.ollama_url, json=payload, stream=True, timeout=300) as resp:
                            resp.raise_for_status()
                            accumulated: list[str] = []
                            # Use decode_unicode=False and decode manually to handle mixed responses
                            for raw in resp.iter_lines(decode_unicode=False):
                                if not raw:
                                    continue
                                # raw may be bytes or str depending on server; normalize to str
                                if isinstance(raw, bytes):
                                    try:
                                        line = raw.decode('utf-8')
                                    except Exception:
                                        line = raw.decode('latin-1', errors='replace')
                                else:
                                    line = str(raw)
                                line = line.strip()
                                try:
                                    print(line, end='', flush=True)
                                except Exception:
                                    pass
                                accumulated.append(line)
                            print()
                            # Attempt to extract JSON "envelope" objects emitted by Ollama
                            # and concatenate their `response` fields. If parsing fails,
                            # fall back to the raw joined stream.
                            responses: list[str] = []
                            for piece in accumulated:
                                piece = piece.strip()
                                if not piece:
                                    continue
                                try:
                                    obj = json.loads(piece)
                                    if isinstance(obj, dict) and 'response' in obj:
                                        responses.append(str(obj['response']))
                                        continue
                                except Exception:
                                    # Not pure JSON - try to find embedded response fields
                                    pass

                                # Regex-extract all "response":"..." occurrences in the piece
                                for m in re.finditer(r'"response"\s*:\s*"(?P<r>(?:\\.|[^"\\])*)"', piece):
                                    raw = m.group('r')
                                    try:
                                        # Decode JSON string escapes safely
                                        decoded = json.loads('"' + raw + '"')
                                    except Exception:
                                        decoded = raw.encode('utf-8', errors='replace').decode('unicode_escape', errors='replace')
                                    responses.append(decoded)

                            if responses:
                                combined = ''.join(responses)
                            else:
                                combined = ''.join(accumulated)

                            class MockResponse:
                                def __init__(self, text: str):
                                    self.text = text

                            return MockResponse(combined)
                    else:
                        response = requests.post(self.ollama_url, json=payload, timeout=300)
                        response.raise_for_status()
                        duration = time.time() - start_time
                        print(f"[INFO] [LLM] Response received in {duration:.2f}s", flush=True)
                        result = response.json()
                        class MockResponse:
                            def __init__(self, text):
                                self.text = text
                        return MockResponse(result["response"])
                except Exception as e:
                    print(f"[WARN] Ollama generation failed (attempt {attempt + 1}): {e}")
                    if attempt < max_retries - 1:
                        time.sleep(2)
                    else:
                        raise e
        elif self.model_choice == "gemini":
            for attempt in range(max_retries):
                try:
                    print(f"[INFO] [LLM] Sending request to Gemini...", flush=True)
                    start_time = time.time()
                    response = self.client.models.generate_content(
                        model=self.current_model_name,
                        contents=prompt
                    )
                    duration = time.time() - start_time
                    print(f"[INFO] [LLM] Response received in {duration:.2f}s", flush=True)
                    return response
                except Exception as e:
                    print(f"[WARN] Gemini generation failed (attempt {attempt + 1}): {e}")
                    if attempt < max_retries - 1:
                        # Exponential backoff for rate limits
                        sleep_time = min(2 ** attempt, 60)  # Max 60 seconds
                        print(f"[INFO] Waiting {sleep_time}s before retry...")
                        time.sleep(sleep_time)
                    else:
                        raise e
        
        elif self.model_choice == "groq":
            for attempt in range(max_retries):
                try:
                    print(f"[INFO] [LLM] Sending request to Groq ({self.groq_model})...", flush=True)
                    start_time = time.time()
                    headers = {
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    }
                    payload = {
                        "model": self.groq_model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 4096,
                        "temperature": 0.1
                    }
                    response = requests.post(self.groq_url, json=payload, headers=headers, timeout=300)
                    response.raise_for_status()
                    result = response.json()
                    duration = time.time() - start_time
                    print(f"[INFO] [LLM] Response received in {duration:.2f}s", flush=True)
                    # Create a mock response object with text attribute
                    class MockResponse:
                        def __init__(self, text):
                            self.text = text
                    return MockResponse(result["choices"][0]["message"]["content"])
                except Exception as e:
                    print(f"[WARN] Groq generation failed (attempt {attempt + 1}): {e}")
                    if attempt < max_retries - 1:
                        # Exponential backoff for rate limits
                        sleep_time = min(2 ** attempt, 60)  # Max 60 seconds
                        print(f"[INFO] Waiting {sleep_time}s before retry...")
                        time.sleep(sleep_time)
                    else:
                        raise e
        
        elif self.model_choice == "github":
            for attempt in range(max_retries):
                try:
                    print(f"[INFO] [LLM] Sending request to GitHub ({self.github_model})...", flush=True)
                    start_time = time.time()
                    headers = {
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    }
                    payload = {
                        "model": self.github_model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 4096,
                        "temperature": 0.1
                    }
                    response = requests.post(self.github_url, json=payload, headers=headers, timeout=300)
                    response.raise_for_status()
                    result = response.json()
                    duration = time.time() - start_time
                    print(f"[INFO] [LLM] Response received in {duration:.2f}s", flush=True)
                    # Create a mock response object with text attribute
                    class MockResponse:
                        def __init__(self, text):
                            self.text = text
                    return MockResponse(result["choices"][0]["message"]["content"])
                except Exception as e:
                    print(f"[WARN] GitHub generation failed (attempt {attempt + 1}): {e}")
                    if attempt < max_retries - 1:
                        # Exponential backoff for rate limits
                        sleep_time = min(2 ** attempt, 60)  # Max 60 seconds
                        print(f"[INFO] Waiting {sleep_time}s before retry...")
                        time.sleep(sleep_time)
                    else:
                        raise e
        
        else:
            raise ValueError("Invalid model choice")

    # Router-style call function - explicit, no fallback
    def call_llm(self, prompt: str):
        return self._try_generate_with_fallback(prompt)

    def build_dependency_map(self, repo_path: str) -> Dict:
        """Build comprehensive repo scan for dependency analysis"""
        import json
        from pathlib import Path
        
        # Prefer canonical workspace analysis artifact if present (work/analysis.json),
        # otherwise fall back to legacy repo-local tests/analysis/analysis.json.
        repo_root_path = Path(repo_path).resolve()
        canonical_analysis = repo_root_path / 'work' / 'analysis.json'
        legacy_analysis = Path(repo_path) / 'tests' / 'analysis' / 'analysis.json'
        if canonical_analysis.exists():
            print(f"ℹ️ Reusing existing canonical analysis from {canonical_analysis}")
            with open(canonical_analysis, 'r') as f:
                return json.load(f)
        if legacy_analysis.exists():
            print(f"ℹ️ Reusing existing analysis from {legacy_analysis}")
            with open(legacy_analysis, 'r') as f:
                return json.load(f)
        else:
            print("🔍 No existing analysis found, performing fresh repo scan...")
            analyzer = DependencyAnalyzer(repo_path)
            return analyzer.perform_repo_scan()

    def generate_tests_for_file(self, file_path: str, repo_path: str, output_dir: str, repo_scan: Dict, validation_feedback: Dict = None, mcdc_decisions: List[Dict] | None = None, *, section_kind: str = "base", decision_meta: Dict | None = None) -> Dict:
        """Generate tests for a SINGLE file with proper context from repo scan"""
        print(f"🤖 Generating tests for {os.path.basename(file_path)}...", flush=True)
        
        analyzer = DependencyAnalyzer(repo_path)
        
        # Get structured analysis (Stage 0)
        scan_results = repo_scan if isinstance(repo_scan, dict) and 'function_index' in repo_scan else None
        # Policy: if a scenarios bundle exists, validate it before synthesis (scenarios -> synthesizer boundary)
        try:
            from pathlib import Path as _P
            import json as _json
            repo_root_path = _P(repo_path).resolve()
            scenarios_path = repo_root_path / 'work' / 'scenarios.json'
            if scenarios_path.exists():
                # Avoid leaking exceptions: use validate_or_halt which exits on failure
                workspace_root = repo_root_path
                schema_path = workspace_root / 'schemas' / 'scenarios.schema.json'
                try:
                    from tools.schema.validate import validate_or_halt
                    scenarios_obj = _json.loads(scenarios_path.read_text(encoding='utf-8'))
                    validate_or_halt(scenarios_obj, str(schema_path), artifact_name='scenarios.json')
                except SystemExit:
                    # propagate validator exit code
                    raise
                except Exception:
                    print(f"[ERROR] Failed to validate scenarios.json at {scenarios_path}")
                    raise
        except Exception:
            # Non-fatal: if validation tooling not available, proceed (but warn)
            print("[WARN] scenarios validation step encountered an error; proceeding without enforced validation.")
        file_analysis_json = analyzer.get_file_analysis(file_path, scan_results)
        
        # Filter for testable functions
        testable_functions = [f for f in file_analysis_json['functions'] if f.get('testable')]
        skipped_functions = [
            {
                'name': f.get('name', ''),
                'reason': f.get('reason', 'Not testable'),
                'category': f.get('category', 'unknown'),
                'hardware_calls': f.get('hardware_calls', []),
            }
            for f in file_analysis_json.get('functions', [])
            if not f.get('testable')
        ]

        hardware_dependencies = set()
        for f in file_analysis_json.get('functions', []):
            for hw in (f.get('hardware_calls') or []):
                if hw:
                    hardware_dependencies.add(str(hw))
        
        if not testable_functions:
             print(f"[SKIP] {os.path.basename(file_path)} has no testable functions.")
             return {
                 'success': False,
                 'reason': 'no_testable_functions',
                 'test_file': None,
                 'functions_that_need_stubs': [],
                 'functions_to_include_directly': [],
                 'skipped_functions': skipped_functions,
                 'hardware_dependencies': sorted(hardware_dependencies),
             }

        # Legacy analysis for prompt construction (includes, stubs, etc.)
        # Handle both repo-scan mode and single-file mode
        if repo_scan and 'function_index' in repo_scan:
            # Multi-file mode: use repo scan data
            rel_file_path = os.path.relpath(file_path, repo_path)
            functions_in_file = []
            called_functions = set()
            
            # Get functions defined in this file
            for func_name, func_info in repo_scan['function_index'].items():
                if func_info.get('file') == rel_file_path:
                    functions_in_file.append({
                        'name': func_name,
                        'signature': func_info.get('signature', ''),
                        'body': func_info.get('body', '')
                    })
            
            # Get functions called by this file (from call graph)
            for caller, callees in repo_scan['call_graph'].items():
                caller_info = repo_scan['function_index'].get(caller, {})
                if caller_info.get('file') == rel_file_path:
                    called_functions.update(callees)
            
            # Get includes for this file
            file_index = repo_scan['file_index']
            includes = file_index.get(rel_file_path, {}).get('includes', [])
        else:
            # Single-file mode: fall back to direct file analysis
            print("[INFO] Single-file mode: performing direct file analysis...", flush=True)
            functions_in_file = analyzer._extract_functions(file_path)
            called_functions = set()
            includes = analyzer._extract_includes(file_path)
            
            # Convert to expected format
            functions_in_file = [{'name': f['name'], 'signature': f.get('signature', ''), 'body': f.get('body', '')} for f in functions_in_file]
        
        analysis = {
            'functions': functions_in_file,
            'called_functions': list(called_functions),
            'includes': includes,
            'file_path': file_path,
            'structured_analysis': file_analysis_json # Pass the structured analysis
        }
        
        print(f"[INFO] Analysis complete: {len(analysis['functions'])} functions found", flush=True)

        # Use repo scan data for decisions (if available)
        if repo_scan and 'hardware_flags' in repo_scan:
            hardware_flags = repo_scan['hardware_flags']
            call_depths = repo_scan['call_depths']
            function_index = repo_scan['function_index']
        else:
            # Single-file mode: provide defaults
            hardware_flags = {}
            call_depths = {}
            function_index = {}
        
        # IDENTIFY FUNCTIONS THAT NEED STUBS AND FUNCTIONS TO INCLUDE DIRECTLY
        functions_that_need_stubs = []
        functions_to_include_directly = []
        implemented_functions = {f['name'] for f in analysis['functions']}

        hw_blocked_deps: list[str] = []
        non_hw_stub_deps: list[str] = []

        for called_func in analysis['called_functions']:
            if called_func not in implemented_functions:
                # Check if it's a hardware dependency (using the set from analyzer)
                # Note: hardware_flags is now Dict[str, Set[str]] or Dict[str, bool] depending on if we updated repo_scan
                # But get_file_analysis handles the set. Here we might still have old format if repo_scan wasn't updated.
                # Assuming repo_scan is updated or we handle it.
                
                is_hw = False
                if isinstance(hardware_flags.get(called_func), set):
                    is_hw = len(hardware_flags.get(called_func)) > 0
                else:
                    is_hw = hardware_flags.get(called_func, False)

                if called_func in function_index and not is_hw and call_depths.get(called_func, 0) <= 2:
                    # Function is defined in repo, hardware-free, and within depth 2 - include directly
                    functions_to_include_directly.append(called_func)
                else:
                    # External, hardware-touching, or too deep - stub or skip
                    functions_that_need_stubs.append(called_func)
                    if is_hw:
                        hw_blocked_deps.append(called_func)
                    else:
                        non_hw_stub_deps.append(called_func)

        # DEBUG: Print what we're sending to the AI
        print(f"[DEBUG] Analysis for {os.path.relpath(file_path, repo_path)}:")
        print(f"[DEBUG] Functions found: {[f['name'] for f in analysis['functions']]}")
        print(f"[DEBUG] Functions to include directly: {functions_to_include_directly}")
        print(f"[DEBUG] Functions that need stubs: {functions_that_need_stubs}")

        print(f"   [INFO] {os.path.basename(file_path)}: {len(analysis['functions'])} functions, {len(functions_to_include_directly)} repo includes, {len(functions_that_need_stubs)} need stubs", flush=True)

        # Always use per-function generation for better performance and modularity.
        per_function_mode = True
        if per_function_mode:
            testable_names = {
                f.get('name')
                for f in (file_analysis_json.get('functions') or [])
                if f.get('testable')
            }
            return self._generate_per_function_tests(
                file_path=file_path,
                repo_path=repo_path,
                output_dir=output_dir,
                analysis=analysis,
                analyzer=analyzer,
                functions_that_need_stubs=functions_that_need_stubs,
                functions_to_include_directly=functions_to_include_directly,
                testable_names=testable_names,
            )

        # Build targeted prompt for this file only
        if mcdc_decisions:
            prompt = self._build_mcdc_prompt(
                analysis,
                functions_that_need_stubs,
                functions_to_include_directly,
                repo_path,
                mcdc_decisions,
                validation_feedback,
            )
        else:
            prompt = self._build_targeted_prompt(analysis, functions_that_need_stubs, functions_to_include_directly, repo_path, validation_feedback)
        print(f"[INFO] Prompt built, calling API...", flush=True)

        # Generate tests using the explicitly selected model
        response = self.call_llm(prompt)
        print(f"✅ API response received from {self.current_model_name}", flush=True)
        test_code = response.text.strip()

        # POST-PROCESSING: Clean up common AI generation issues
        test_code = self._post_process_test_code(test_code, analysis, analysis['includes'])

        # Harden against a common compilation failure: TEST_F refers to a fixture class
        # that doesn't exist in the file.
        test_code = self._sanitize_gtest_fixture_usage(test_code)

        # Additional hardening against common C++ hallucinations.
        if self._detect_language(file_path) != 'c':
            test_code = self._sanitize_cpp_interface_hallucinations(test_code)
            test_code = self._normalize_project_namespace_qualifiers(test_code, analysis.get('includes', []), repo_path=repo_path)
            test_code = self._disable_tests_calling_private_methods(test_code)

        # Run post-generation cleanup pass to normalize includes, namespaces, and formatting
        test_code = self._post_generation_cleanup(test_code, file_path)
        self._enforce_post_cleanup_rules(test_code, file_path=file_path, context="whole-file")

        # Add dependency notes (informational only; not a coverage truth)
        if hw_blocked_deps:
            test_code += f"\n// Not directly included (hardware-touching): {', '.join(sorted(set(hw_blocked_deps)))}"
        if non_hw_stub_deps:
            test_code += f"\n// Not directly included (needs stubs/external/too deep): {', '.join(sorted(set(non_hw_stub_deps)))}"

        # Remove gtest includes for C files (Unity tests)
        if self._detect_language(file_path) == 'c':
            test_code = test_code.replace('#include <gtest/gtest.h>\n', '')
            test_code = test_code.replace('#include <gtest/gtest.h>', '')

        # Save test file (mirror repo-relative source folder structure under <repo>/tests/)
        repo_root = Path(repo_path).resolve()
        src_path = Path(file_path).resolve()
        out_base = Path(output_dir)
        if not out_base.is_absolute():
            out_base = repo_root / out_base

        # If the source file lives under a top-level subproject (e.g., RailwaySignalSystem/src/...)
        # prefer placing tests under that subproject's tests/ hierarchy instead of the workspace root.
        try:
            source_rel = src_path.relative_to(repo_root)
            if len(source_rel.parts) > 1:
                top = source_rel.parts[0]
                candidate = repo_root / top
                if candidate.is_dir():
                    # Use the subproject tests folder, mirroring the src layout under tests/src
                    out_base = candidate / output_dir
        except Exception:
            # If we can't relativize or detect subproject, keep default out_base
            pass

        try:
            source_rel = src_path.relative_to(repo_root)
        except Exception:
            source_rel = Path(src_path.name)

        test_filename = f"test_{src_path.name}"
        output_path = out_base / source_rel.parent / test_filename
        # V2: Append-only, section-based approvals. Never overwrite approved content;
        # instead, append a new unapproved section and mark it active in tests/.approvals.json.
        source_rel_str = repo_relpath(src_path, repo_root)
        test_file_rel_str = repo_relpath(output_path, repo_root)
        source_sha = sha256_file(src_path)

        # If the source changed since previously-approved content, deactivate old sections.
        registry = ApprovalsRegistry(repo_root)
        registry.load()
        registry.deactivate_if_source_changed(source_rel=source_rel_str, new_source_sha256=source_sha)

        kind_prefix = (section_kind or "base").lower()

        # Demo-safe, human-friendly naming:
        # - no hashes / fingerprints
        # - no decision expressions / line numbers in headers
        # - deterministic ordinal per (test file, kind) to avoid collisions
        existing_same_kind = [
            s
            for s in registry.iter_sections()
            if s.get("test_file_rel") == test_file_rel_str and (s.get("kind") or "base") == kind_prefix
        ]
        section_index = len(existing_same_kind) + 1

        if kind_prefix == "base":
            section_display_name = "BASE_TESTS"
        elif kind_prefix == "mcdc":
            section_display_name = "MCDC_TESTS"
        elif kind_prefix == "boundary":
            section_display_name = "BOUNDARY_TESTS"
        elif kind_prefix == "error_path":
            section_display_name = "ERROR_PATH_TESTS"
        else:
            section_display_name = "GENERATED_TESTS"

        # Internal, non-demo-facing name stored in the registry.
        section_name = f"{kind_prefix}_{section_index}"

        suffix = "" if section_index == 1 else f"_{section_index}"
        namespace = f"ai_test_section_{kind_prefix}{suffix}"
        suite_prefix = f"AISEC_{kind_prefix.upper()}{suffix}_"

        def _to_ident(s: str) -> str:
            s2 = re.sub(r"[^A-Za-z0-9_]", "_", (s or ""))
            s2 = re.sub(r"_+", "_", s2).strip("_")
            if not s2:
                return "Test"
            if s2[0].isdigit():
                return f"T_{s2}"
            return s2

        # Standardize the fixture base name (readable) while preserving the AISEC_<id>_ prefix.
        source_stem = _to_ident(src_path.stem)
        kind_label = "Base"
        if kind_prefix == "mcdc":
            kind_label = f"Mcdc_Decision_{section_index}"
        elif kind_prefix == "boundary":
            kind_label = "Boundary"
        elif kind_prefix == "error_path":
            kind_label = "ErrorPath"

        preferred_fixture_base = f"{source_stem}Test_{kind_label}"

        section_block, section_sha = build_section_block(
            raw_test_code=test_code,
            section_name=section_display_name,
            source_rel=source_rel_str,
            section_namespace=namespace,
            suite_prefix=suite_prefix,
            preferred_fixture_base=preferred_fixture_base,
            approved=False,
        )

        append_section_to_file(output_path, section_block)

        registry.upsert_section(
            section_sha256=section_sha,
            name=section_name,
            test_file_rel=test_file_rel_str,
            source_rel=source_rel_str,
            source_sha256=source_sha,
            approved=False,
            active=True,
            kind=kind_prefix,
            decision=decision_meta,
        )
        registry.save()

        # Best-effort: write/update a safety summary artifact.
        try:
            from .safety_policy import save_safety_summary

            generated_kind = (section_kind or "base").lower()
            update: dict = {
                "safety_level": getattr(self.safety_policy, "safety_level", None) or "(unspecified)",
                "human_approvals_complete": False,
            }
            if generated_kind == "base":
                update["base_tests_generated"] = True
            elif generated_kind == "mcdc":
                update["mcdc_tests_generated"] = True
            elif generated_kind == "boundary":
                update["boundary_tests_generated"] = True
            elif generated_kind == "error_path":
                update["error_path_tests_generated"] = True

            save_safety_summary(repo_root, update)
        except Exception:
            pass

        print(f"✅ Test saved to {output_path}", flush=True)
        # Add dependency context to returned metadata for downstream review artifacts.
        for dep in functions_that_need_stubs:
            if dep:
                hardware_dependencies.add(str(dep))

        return {
            'success': True,
            'test_file': str(output_path),
            'functions_that_need_stubs': functions_that_need_stubs,
            'functions_to_include_directly': functions_to_include_directly,
            'skipped_functions': skipped_functions,
            'hardware_dependencies': sorted(hardware_dependencies),
        }

    def _build_targeted_prompt(self, analysis: Dict, functions_that_need_stubs: List[str], functions_to_include_directly: List[str], repo_path: str, validation_feedback: Dict = None) -> str:
        """Build targeted prompt based on detected programming language"""
        file_path = analysis.get('file_path', '')
        language = self._detect_language(file_path)
        print(f"[INFO] Detected language for {file_path}: {language}", flush=True)

        if language == 'c':
            print("[INFO] Using Unity prompt for C file", flush=True)
            return self._build_unity_prompt(analysis, functions_that_need_stubs, functions_to_include_directly, repo_path, validation_feedback)
        else:  # cpp or default
            print("[INFO] Using Google Test prompt for C++ file", flush=True)
            return self._build_gtest_prompt(analysis, functions_that_need_stubs, functions_to_include_directly, repo_path, validation_feedback)

    def _build_mcdc_prompt(
        self,
        analysis: Dict,
        functions_that_need_stubs: List[str],
        functions_to_include_directly: List[str],
        repo_path: str,
        mcdc_decisions: List[Dict],
        validation_feedback: Dict = None,
    ) -> str:
        """Build a focused MC/DC prompt for a single file.

        This is an AI-assisted approach (not tool-qualified MC/DC measurement).
        It instructs the model to generate test pairs that toggle each condition.
        """

        base = self._build_gtest_prompt(
            analysis,
            functions_that_need_stubs,
            functions_to_include_directly,
            repo_path,
            validation_feedback,
        )

        rel_path = os.path.relpath(analysis.get('file_path', ''), repo_path)
        decisions_text = json.dumps(mcdc_decisions, indent=2)

        overlay = f"""


    ==============================
    MC/DC EXTENSION (MANDATORY)
    ==============================
    You are now generating an *additional* set of tests specifically targeted at MC/DC for the following file:
    - Source file: {rel_path}

    The analyzer identified the following candidate decisions (line numbers, expressions, atomic conditions):
    {decisions_text}

    REQUIREMENTS:
    1) For each decision, generate MC/DC-style test PAIRS such that each atomic condition independently affects the decision outcome.
    2) Each pair MUST differ in only ONE condition while keeping other conditions constant.
    3) Prefer calling real public APIs/functions from this file. Do not invent functions.
        4) Keep tests realistic and compilable. If a decision cannot be driven directly, add a TODO comment explaining why.
        5) Output a complete compilable GTest translation unit fragment (includes + tests). Avoid defining main().

        HARD CONSTRAINTS (DO NOT VIOLATE):
        - Do NOT define your own versions of production/HAL types (e.g., do not create fake `enum class PinMode`, `enum class PinLevel`, or `class IGpio`).
            Use the real headers already included by the base prompt.
        - Do NOT invent enum members (e.g., don't reference `PinMode::Analog` or `PinMode::OutputOpenDrain` unless they exist in the real header).
        - Do NOT call private/protected methods. If a helper isn't public in the header, test it indirectly via public APIs.
        - If you implement a fake that inherits an interface, implement *all* pure virtuals exactly (including `const` qualifiers).
            Example class of failure: `read(Pin) const` missing `const`.
    """

        return base + overlay

    @staticmethod
    def _sanitize_cpp_interface_hallucinations(test_code: str) -> str:
        """Deterministic cleanups for common C++ interface hallucinations.

        Goal: prevent known recurring compile breaks from reaching the build.
        This is intentionally conservative and repo-agnostic.
        """

        if not test_code:
            return test_code

        # Common hallucinated enum members (esp. when a model assumes a richer HAL).
        test_code = re.sub(
            r"((?:::)?[A-Za-z_][A-Za-z0-9_]*::hal::PinMode::)OutputOpenDrain\b",
            r"\1OutputPushPull",
            test_code,
        )
        test_code = re.sub(
            r"((?:::)?[A-Za-z_][A-Za-z0-9_]*::hal::PinMode::)Analog\b",
            r"\1Input",
            test_code,
        )

        # Fix missing const on IGpio::read overrides (seen frequently).
        test_code = re.sub(
            r"(((?:::)?[A-Za-z_][A-Za-z0-9_]*::hal::PinLevel\s+read\s*\(\s*(?:::)?[A-Za-z_][A-Za-z0-9_]*::hal::Pin\s+[^\)]*\)\s*))override",
            r"\1const override",
            test_code,
        )

        # Ensure any IGpio-derived fake implements read(Pin) const.
        # This injects a minimal stub if missing.
        class_re = re.compile(
            r"(class\s+[A-Za-z_][A-Za-z0-9_]*\s*:\s*public\s+(?P<ns>(?:::)?[A-Za-z_][A-Za-z0-9_]*)::hal::IGpio\s*\{)(?P<body>.*?)(\n\};)",
            re.DOTALL,
        )

        def _inject_read(m: re.Match) -> str:
            head = m.group(1)
            ns = m.group("ns")
            body = m.group("body")
            tail = m.group(3)
            if re.search(r"\bread\s*\(", body):
                return m.group(0)
            injection = (
                f"\n    {ns}::hal::PinLevel read({ns}::hal::Pin /*pin*/) const override {{\n"
                f"        return {ns}::hal::PinLevel::Low;\n"
                "    }\n"
            )
            return head + body + injection + tail

        test_code = class_re.sub(_inject_read, test_code)

        # Common hallucination: treat evaluateControllerLogic(...) return value as an enum.
        # Rewrite enum-style EXPECT_EQ into an aspect assertion that compiles and still
        # checks safety behavior, without hardcoding a project namespace.
        call_re = re.compile(
            r"(?P<indent>^\s*)(?P<macro>(?:EXPECT|ASSERT)_EQ)\s*\(\s*(?P<ns>(?:::)?[A-Za-z_][A-Za-z0-9_]*)::logic::evaluateControllerLogic\s*\((?P<args>.*?)\)\s*,\s*(?P<expected>[^\)]+)\)\s*;\s*$",
            re.DOTALL | re.MULTILINE,
        )

        counter = 0

        def _rewrite_controllerlogic_expect(m: re.Match) -> str:
            nonlocal counter
            expected = m.group("expected")
            aspect = None
            if re.search(r"\bPROCEED\b", expected):
                aspect = "Clear"
            elif re.search(r"\bWARNING\b", expected):
                aspect = "Caution"
            elif re.search(r"\bSTOP\b", expected):
                aspect = "Stop"
            else:
                return m.group(0)

            counter += 1
            indent = m.group("indent")
            ns = m.group("ns")
            args = m.group("args").strip()
            out_name = f"out_{counter}"
            return (
                f"{indent}const auto {out_name} = {ns}::logic::evaluateControllerLogic({args});\n"
                f"{indent}EXPECT_EQ({out_name}.aspect, {ns}::drivers::Aspect::{aspect});"
            )

        test_code = call_re.sub(_rewrite_controllerlogic_expect, test_code)
        return test_code

    @staticmethod
    def _normalize_project_namespace_qualifiers(
        test_code: str,
        source_includes: list[str] | None = None,
        repo_path: str | None = None,
    ) -> str:
        """Normalize hallucinated root namespaces using include-path hints.

        If includes clearly indicate a single root namespace (e.g., doors/... or railway/...)
        rewrite mismatched qualifiers like ::other::logic::X to ::<root>::logic::X.
        When possible, detect nested namespaces from headers (e.g., root::logic) and
        correct symbols that were emitted as ::root::Symbol.
        """

        if not test_code:
            return test_code

        roots: list[str] = []
        include_pattern = re.compile(r"#include\s+[\"<]([A-Za-z_][A-Za-z0-9_]*)/")
        include_path_pattern = re.compile(r"#include\s+[\"<]([^\">]+)[\">]")
        include_paths: list[str] = []

        for m in include_pattern.finditer(test_code):
            roots.append(m.group(1))

        for m in include_path_pattern.finditer(test_code):
            include_paths.append(m.group(1).strip())

        for inc in (source_includes or []):
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)/", str(inc).strip())
            if m:
                roots.append(m.group(1))
                include_paths.append(str(inc).strip())

        ignored = {
            "gtest", "gmock", "googletest", "googlemock", "std", "cstddef", "cstdint", "stdint", "stddef"
        }
        roots = [r for r in roots if r not in ignored]
        if not roots:
            return test_code

        freq: dict[str, int] = {}
        for root in roots:
            freq[root] = freq.get(root, 0) + 1

        preferred = max(freq.items(), key=lambda item: item[1])[0]

        ns_re = re.compile(r"(?<![A-Za-z0-9_])(?:::)?(?P<root>[A-Za-z_][A-Za-z0-9_]*)::(?=(logic|hal|drivers|app|platform)\b)")
        observed = {m.group("root") for m in ns_re.finditer(test_code)}
        for root in observed:
            if root == preferred:
                continue
            test_code = re.sub(
                rf"(?<![A-Za-z0-9_])(?:::)?{re.escape(root)}::(?=(logic|hal|drivers|app|platform)\b)",
                f"::{preferred}::",
                test_code,
            )

        if repo_path and include_paths:
            repo_root = Path(repo_path).resolve()

            def _resolve_include(include_path: str) -> Path | None:
                candidates = [
                    repo_root / include_path,
                    repo_root / "include" / include_path,
                    repo_root / "src" / include_path,
                ]
                for candidate in candidates:
                    if candidate.is_file():
                        return candidate
                return None

            def _extract_namespace_symbols(header_text: str, namespace_qualifier: str) -> set[str]:
                symbols: set[str] = set()
                in_namespace = False
                pending_open = False
                depth = 0
                ns_re = re.compile(rf"\bnamespace\s+{re.escape(namespace_qualifier)}\b")

                def _collect(line: str) -> None:
                    for pattern in (
                        r"\benum\s+class\s+([A-Za-z_][A-Za-z0-9_]*)",
                        r"\benum\s+([A-Za-z_][A-Za-z0-9_]*)",
                        r"\bstruct\s+([A-Za-z_][A-Za-z0-9_]*)",
                        r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)",
                        r"\busing\s+([A-Za-z_][A-Za-z0-9_]*)\b",
                        r"\btypedef\b[^;]*\b([A-Za-z_][A-Za-z0-9_]*)\s*;",
                        r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*;",
                    ):
                        m = re.search(pattern, line)
                        if m:
                            symbols.add(m.group(1))

                for line in header_text.splitlines():
                    if not in_namespace:
                        if ns_re.search(line):
                            if "{" in line:
                                in_namespace = True
                                depth = line.count("{") - line.count("}")
                            else:
                                in_namespace = True
                                pending_open = True
                            continue

                    if pending_open:
                        if "{" in line:
                            pending_open = False
                            depth = line.count("{") - line.count("}")
                        continue

                    if in_namespace and depth > 0:
                        _collect(line)
                        depth += line.count("{") - line.count("}")
                        if depth <= 0:
                            in_namespace = False
                            pending_open = False

                return symbols

            symbols: set[str] = set()
            include_re = re.compile(rf"^{re.escape(preferred)}/logic/.*")

            for inc in include_paths:
                inc = str(inc).strip()
                if not include_re.match(inc):
                    continue
                header_path = _resolve_include(inc)
                if not header_path:
                    continue
                try:
                    header_text = header_path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                symbols.update(_extract_namespace_symbols(header_text, f"{preferred}::logic"))

            if symbols:
                for sym in sorted(symbols):
                    pattern = re.compile(
                        rf"(?<![A-Za-z0-9_])(?P<prefix>(?:::)?){re.escape(preferred)}::(?!logic::){re.escape(sym)}\b"
                    )
                    test_code = pattern.sub(lambda m: f"{m.group('prefix')}{preferred}::logic::{sym}", test_code)

        return test_code

    @staticmethod
    def _disable_tests_calling_private_methods(test_code: str, method_names: list[str] | None = None) -> str:
        """Disable generated tests that call likely-private helpers.

        We can't reliably parse C++ access specifiers here. Instead, we disable
        tests containing known private helper names, defaulting to common offenders.
        """

        if not test_code:
            return test_code

        targets = set(method_names or [])
        # Default fallback for recurring private helper names in this project.
        targets.update({"writeLamp"})

        lines = test_code.splitlines()
        out: list[str] = []

        in_test = False
        brace_depth = 0
        pending_macro_line: str | None = None
        test_block: list[str] = []

        test_macro_re = re.compile(r"^\s*TEST(?:_F|_P)?\s*\(")

        def _block_contains_private_call(block_lines: list[str]) -> bool:
            block_text = "\n".join(block_lines)
            for name in targets:
                if re.search(rf"\b{name}\s*\(", block_text):
                    return True
            return False

        def _disable_macro(macro_line: str) -> str:
            # Insert DISABLED_ prefix into the test name argument.
            # Works for TEST, TEST_F, TEST_P.
            m = re.match(
                r"^(\s*TEST(?:_F|_P)?\s*\(\s*[^,]+\s*,\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*\)\s*\{?\s*)$",
                macro_line,
            )
            if not m:
                return macro_line
            prefix, name, suffix = m.group(1), m.group(2), m.group(3)
            if name.startswith("DISABLED_"):
                return macro_line
            return f"{prefix}DISABLED_{name}{suffix}"

        for line in lines:
            if not in_test and test_macro_re.match(line):
                in_test = True
                brace_depth = 0
                pending_macro_line = line
                test_block = []
                # Count braces starting from macro line in case it includes '{'.
                brace_depth += line.count("{") - line.count("}")
                continue

            if in_test:
                test_block.append(line)
                brace_depth += line.count("{") - line.count("}")
                if brace_depth <= 0:
                    # End of test block.
                    disable = _block_contains_private_call(test_block)
                    if pending_macro_line is not None:
                        out.append(_disable_macro(pending_macro_line) if disable else pending_macro_line)
                    out.extend(test_block)
                    in_test = False
                    pending_macro_line = None
                    test_block = []
                continue

            out.append(line)

        # If file ended mid-test, flush best-effort.
        if in_test and pending_macro_line is not None:
            disable = _block_contains_private_call(test_block)
            out.append(_disable_macro(pending_macro_line) if disable else pending_macro_line)
            out.extend(test_block)

        return "\n".join(out)

    @staticmethod
    def _sanitize_gtest_fixture_usage(test_code: str) -> str:
        """Best-effort sanitizer to prevent non-compiling TEST_F fixture mismatches.

        Some generated code defines a fixture class but uses a different/non-existent
        fixture name in TEST_F macros. That causes hard compilation errors.

        Strategy:
        - Collect fixture class names that derive from ::testing::Test or testing::Test.
        - Ensure fixture-based tests stay fixture-based:
            - For each TEST_F(Fixture, ...):
                - If Fixture exists, keep.
                - Else if any fixture exists, rewrite to a known fixture (prefer the only fixture).
                - Else leave unchanged.
            - If exactly one fixture exists, rewrite TEST(Suite, Name) -> TEST_F(Fixture, Name)
              so generated files don't mix TEST/TEST_F when a fixture is present.
        """

        if not test_code:
            return test_code

        fixture_pattern = re.compile(
            r"\bclass\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*public\s+(?:::)?testing::Test\b"
        )
        fixtures = [m.group("name") for m in fixture_pattern.finditer(test_code)]
        fixtures_set = set(fixtures)
        single_fixture = fixtures[0] if len(fixtures) == 1 else None
        preferred_fixture = single_fixture or (fixtures[0] if fixtures else None)

        test_f_pattern = re.compile(
            r"\bTEST_F\(\s*(?P<fixture>[A-Za-z_][A-Za-z0-9_]*)\s*,\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\)"
        )

        def _rewrite(match: re.Match) -> str:
            fixture = match.group("fixture")
            name = match.group("name")
            if fixture in fixtures_set:
                return match.group(0)
            if preferred_fixture:
                return f"TEST_F({preferred_fixture}, {name})"
            return match.group(0)

        updated = test_f_pattern.sub(_rewrite, test_code)

        # If the file defines exactly one fixture, prefer TEST_F for all tests.
        if single_fixture:
            test_pattern = re.compile(
                r"\bTEST\(\s*(?P<suite>[A-Za-z_][A-Za-z0-9_]*)\s*,\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\)"
            )

            def _rewrite_test(m: re.Match) -> str:
                name = m.group("name")
                return f"TEST_F({single_fixture}, {name})"

            updated = test_pattern.sub(_rewrite_test, updated)

        return updated

    @staticmethod
    def _sanitize_manual_gtest_setup_calls(test_code: str) -> str:
        """Remove manual SetUp/TearDown calls inside TEST_F/TEST_P bodies.

        Calling SetUp()/TearDown() manually inside a test body is almost always
        a bug in generated code: it can reset fake state and invalidate test
        preconditions, leading to confusing failures.

        This sanitizer is conservative:
        - Only affects calls inside TEST_F/TEST_P blocks (not fixture method definitions).
        - Removes standalone calls like `SetUp();`, `this->SetUp();`, and
          `ASSERT_NO_THROW(SetUp());` / `EXPECT_NO_THROW(SetUp());`.
        """

        if not test_code:
            return test_code

        test_macro_re = re.compile(r"^\s*TEST_(?:F|P)\s*\(")
        bare_call_re = re.compile(
            r"^\s*(?:this->)?(?:SetUp|TearDown)\s*\(\s*\)\s*;\s*(?://.*)?$"
        )
        no_throw_call_re = re.compile(
            r"^\s*(?:ASSERT|EXPECT)_NO_THROW\s*\(\s*(?:this->)?(?:SetUp|TearDown)\s*\(\s*\)\s*\)\s*;\s*(?://.*)?$"
        )

        lines = test_code.splitlines()
        cleaned: list[str] = []

        in_test = False
        seen_open_brace = False
        brace_depth = 0

        for line in lines:
            # Enter a TEST_F/TEST_P block.
            if not in_test and test_macro_re.match(line):
                in_test = True
                seen_open_brace = False
                brace_depth = 0
                cleaned.append(line)
                brace_depth += line.count("{") - line.count("}")
                if "{" in line:
                    seen_open_brace = True
                if seen_open_brace and brace_depth <= 0:
                    in_test = False
                continue

            if in_test:
                # Before the opening '{', copy lines verbatim (signature formatting, comments, etc.).
                if not seen_open_brace:
                    cleaned.append(line)
                    brace_depth += line.count("{") - line.count("}")
                    if "{" in line:
                        seen_open_brace = True
                    if seen_open_brace and brace_depth <= 0:
                        in_test = False
                    continue

                # Inside the test body: drop manual SetUp/TearDown calls.
                if bare_call_re.match(line) or no_throw_call_re.match(line):
                    brace_depth += line.count("{") - line.count("}")
                    if brace_depth <= 0:
                        in_test = False
                    continue

                cleaned.append(line)
                brace_depth += line.count("{") - line.count("}")
                if brace_depth <= 0:
                    in_test = False
                continue

            cleaned.append(line)

        return "\n".join(cleaned)

    def _post_generation_cleanup(self, test_code: str, file_path: str) -> str:
        """Run the deterministic cleanup pass via the configured LLM.

        If the LLM call fails, return the original `test_code` unchanged.
        """
        if not test_code:
            return test_code
        try:
            prompt = self.post_generation_cleanup_prompt + "\n\n" + test_code
            print(f"[INFO] Running post-generation cleanup for {file_path}...", flush=True)
            resp = self.call_llm(prompt)
            cleaned = getattr(resp, 'text', None)
            if cleaned:
                return cleaned.strip()
            return test_code
        except Exception as e:
            print(f"[WARN] Post-generation cleanup failed: {e}", flush=True)
            return test_code

    def _enforce_post_cleanup_rules(self, test_code: str, *, file_path: str, context: str | None = None) -> None:
        """Hard validation after cleanup to stop markdown/namespace/helper regressions."""

        if not test_code:
            return

        issues: list[str] = []
        if "```" in test_code:
            issues.append("Markdown fence detected (```)")
        if re.search(r"\busing\s+namespace\b", test_code):
            issues.append("`using namespace` is forbidden in generated tests")
        if re.search(r"\bcreateInputs\s*\(", test_code):
            issues.append("Invented helper `createInputs` detected")

        if issues:
            ctx = f" ({context})" if context else ""
            raise RuntimeError(
                f"Post-cleanup validation failed for {file_path}{ctx}: " + "; ".join(issues)
            )

    @staticmethod
    def _sanitize_stray_gtest_macro_lines(test_code: str) -> str:
        """Remove stray, standalone GoogleTest macro tokens.

        Occasionally an LLM emits a bare `TEST_F` (or `TEST`) on its own line.
        That yields confusing compiler errors and provides no useful intent.
        """

        if not test_code:
            return test_code

        lines = test_code.splitlines()
        cleaned: list[str] = []
        for line in lines:
            if re.match(r"^\s*TEST_F\s*$", line):
                continue
            if re.match(r"^\s*TEST\s*$", line):
                continue
            if re.match(r"^\s*TEST_P\s*$", line):
                continue
            cleaned.append(line)
        return "\n".join(cleaned)

    @staticmethod
    def _sanitize_railway_igpio_fake(test_code: str) -> str:
        """Best-effort fixups for `<project>::hal::IGpio` fake implementations.

        Ensures common FakeGpio implementations match the real interface:
        - Pin type is `<project>::hal::Pin`
        - read(Pin) is const
        - write(Pin, PinLevel) exists (some models omit it)
        """

        if not test_code:
            return test_code

        if not re.search(r'"[A-Za-z_][A-Za-z0-9_]*/hal/IGpio\.h"', test_code):
            return test_code
        if "class FakeGpio" not in test_code:
            return test_code

        ns_match = re.search(r'class\s+FakeGpio\s*:\s*public\s+(?P<ns>(?:::)?[A-Za-z_][A-Za-z0-9_]*)::hal::IGpio\b', test_code)
        if not ns_match:
            return test_code
        ns = ns_match.group('ns').lstrip(':')

        # Function signatures (some models guess uint8_t/uint16_t/int for pins).
        pin_guess = r"(?:(?:std::)?u?int(?:8|16|32)_t|unsigned\s+char|unsigned\s+short|unsigned\s+int|int|short|char)"

        test_code = re.sub(
            rf"\bvoid\s+configure\s*\(\s*{pin_guess}\s+pin\s*,\s*(?:::)?{re.escape(ns)}::hal::PinMode\s+mode\s*\)\s*override",
            f"void configure({ns}::hal::Pin pin, {ns}::hal::PinMode mode) override",
            test_code,
        )

        # read(): ensure Pin type and const override.
        test_code = re.sub(
            rf"\b(?:::)?{re.escape(ns)}::hal::PinLevel\s+read\s*\(\s*{pin_guess}\s+pin\s*\)\s*(?:const\s*)?override",
            f"{ns}::hal::PinLevel read({ns}::hal::Pin pin) const override",
            test_code,
        )
        test_code = re.sub(
            rf"\b(?:::)?{re.escape(ns)}::hal::PinLevel\s+read\s*\(\s*(?:::)?{re.escape(ns)}::hal::Pin\s+pin\s*\)\s*override",
            f"{ns}::hal::PinLevel read({ns}::hal::Pin pin) const override",
            test_code,
        )

        # write(): ensure Pin type when present.
        test_code = re.sub(
            rf"\bvoid\s+write\s*\(\s*{pin_guess}\s+pin\s*,\s*(?:::)?{re.escape(ns)}::hal::PinLevel\s+level\s*\)\s*override",
            f"void write({ns}::hal::Pin pin, {ns}::hal::PinLevel level) override",
            test_code,
        )

        # Member declarations (common names)
        test_code = re.sub(
            r"\buint8_t\s+configuredPin_\b",
            f"{ns}::hal::Pin configuredPin_",
            test_code,
        )
        test_code = re.sub(
            r"\buint8_t\s+lastWritePin_\b",
            f"{ns}::hal::Pin lastWritePin_",
            test_code,
        )
        # When read() is const, lastReadPin_ must be mutable if we track it.
        test_code = re.sub(
            r"\buint8_t\s+lastReadPin_\b",
            f"mutable {ns}::hal::Pin lastReadPin_",
            test_code,
        )
        test_code = re.sub(
            rf"\b{re.escape(ns)}::hal::Pin\s+lastReadPin_\b",
            f"mutable {ns}::hal::Pin lastReadPin_",
            test_code,
        )

        # Some models omit the write() override entirely; add a minimal stub so the fake compiles.
        class_pattern = re.compile(
            rf"(class\s+FakeGpio\s*:\s*public\s+(?:::)?{re.escape(ns)}::hal::IGpio\s*\{{)(?P<body>.*?)(^\s*\}};)",
            flags=re.DOTALL | re.MULTILINE,
        )

        def _ensure_write(m: re.Match) -> str:
            body = m.group("body")
            if re.search(r"\bvoid\s+write\s*\(", body):
                return m.group(0)

            # Guess indentation from existing methods.
            indent = "    "
            im = re.search(r"\n(?P<ws>\s+)void\s+configure\b", body)
            if im:
                indent = im.group("ws")
            else:
                im = re.search(rf"\n(?P<ws>\s+)(?:::)?{re.escape(ns)}::hal::PinLevel\s+read\b", body)
                if im:
                    indent = im.group("ws")

            insertion = (
                f"\n{indent}void write({ns}::hal::Pin pin, {ns}::hal::PinLevel level) override "
                f"{{ (void)pin; (void)level; }}\n"
            )
            return m.group(1) + body + insertion + m.group(3)

        test_code = class_pattern.sub(_ensure_write, test_code, count=1)

        return test_code

    @staticmethod
    def _sanitize_trackcircuitinput_config_designators(test_code: str) -> str:
        """Fix designated-initializer ordering for TrackCircuitInput::Config.

        GCC (especially in -std=gnu++17 mode) can error if designators are out of
        declaration order. The declaration order in this repo is:
        pin, activeLow, debounceMs, stuckLowFaultMs.
        """

        if not test_code or "TrackCircuitInput::Config" not in test_code:
            return test_code

        order = ["pin", "activeLow", "debounceMs", "stuckLowFaultMs"]

        pattern = re.compile(
            r"(?P<prefix>TrackCircuitInput::Config\s+cfg_\s*=\s*\{\s*\n)"
            r"(?P<body>.*?)"
            r"(?P<suffix>\n\s*\}\s*;)",
            flags=re.DOTALL,
        )

        def _rewrite(m: re.Match) -> str:
            body = m.group("body")
            lines = body.splitlines()

            picked: dict[str, str] = {}
            other_lines: list[str] = []
            for line in lines:
                dm = re.match(r"^\s*\.(?P<field>[A-Za-z_][A-Za-z0-9_]*)\s*=.*", line)
                if dm:
                    field = dm.group("field")
                    if field in order and field not in picked:
                        picked[field] = line
                        continue
                other_lines.append(line)

            rebuilt: list[str] = []
            for field in order:
                if field in picked:
                    rebuilt.append(picked[field])
            rebuilt.extend(other_lines)

            return m.group("prefix") + "\n".join(rebuilt) + m.group("suffix")

        return pattern.sub(_rewrite, test_code)

    def _build_function_only_prompt(
        self,
        *,
        analysis: Dict,
        function_info: Dict,
        functions_that_need_stubs: List[str],
        functions_to_include_directly: List[str],
        repo_path: str,
        validation_feedback: Dict = None,
    ) -> str:
        """Build a prompt that targets a single function only."""

        rel_path = os.path.relpath(analysis['file_path'], repo_path)
        function_name = function_info.get('name', '')
        return_type = function_info.get('return_type', '').strip() or 'void'
        params = function_info.get('parameters', '').strip()
        body = function_info.get('body', '').strip()
        includes = analysis.get('includes', []) or []

        validation_feedback_section = "NONE - First generation attempt"
        if validation_feedback:
            issues = validation_feedback.get('issues', [])
            if issues:
                validation_feedback_section = "PREVIOUS ATTEMPT FAILED WITH THESE SPECIFIC ISSUES - FIX THEM:\n" + "\n".join(
                    f"- {issue}" for issue in issues[:5]
                )

        prompt = f"""
You are an expert C++ unit test generation agent for embedded systems.

CRITICAL REQUIREMENTS:
- Generate tests ONLY for the single function listed below.
- Do NOT include tests for any other function.
- Output ONLY valid C++ test code (no markdown, no explanations).
- Do NOT define main().

TARGET FUNCTION:
File: {rel_path}
Signature: {return_type} {function_name}({params})

FUNCTION BODY (context for behavior only):
{body}

AVAILABLE INCLUDES (use only if needed):
{chr(10).join(f"- {inc}" for inc in includes) or "- None"}

REPO FUNCTIONS TO INCLUDE DIRECTLY (call these directly; headers exist):
{chr(10).join(f"- {func_name}" for func_name in functions_to_include_directly) or "- None"}

EXTERNAL FUNCTIONS TO STUB (only these; infer signatures from calls if needed):
{chr(10).join(f"- {func_name}" for func_name in functions_that_need_stubs) or "- None"}

VALIDATION FEEDBACK (if any):
{validation_feedback_section}

OUTPUT RULES:
- Include necessary #includes.
- Define a fixture only if needed.
- Keep all tests scoped to the target function.
- Do not reference any other production function unless it is explicitly listed above.
- No markdown fences/backticks.
- Do NOT invent helper factories (e.g., createInputs); construct input structs inline using real project types.
- Never use `using namespace`; fully qualify every symbol (e.g., ::project_ns::logic::StopReason::None).
- Exactly one include block per test section (<gtest/gtest.h> first, then production headers).
- Use `TEST` by default; only use fixtures if shared mutable state is mandatory and reset inputs each test.
- Call the production function exactly once per test, store the result, then assert on its fields.
"""

        return prompt

    @staticmethod
    def _function_block_markers(func_id: str) -> tuple[str, str]:
        begin = f"// === BEGIN TESTS: {func_id} ==="
        end = f"// === END TESTS: {func_id} ==="
        return begin, end

    @staticmethod
    def _extract_function_blocks(text: str) -> dict[str, str]:
        blocks: dict[str, str] = {}
        if not text:
            return blocks
        pattern = re.compile(
            r"^// === BEGIN TESTS: (?P<id>.+?) ===\s*$\n(?P<body>.*?)^// === END TESTS: \1 ===\s*$",
            flags=re.MULTILINE | re.DOTALL,
        )
        for m in pattern.finditer(text):
            func_id = m.group("id").strip()
            blocks[func_id] = m.group(0)
        return blocks

    @staticmethod
    def _build_master_test_content(source_rel: str, ordered_blocks: List[str]) -> str:
        lines: List[str] = [
            "/* Auto-generated master test file. Do not edit by hand. */",
            f"/* Source: {source_rel} */",
            "",
        ]
        lines.extend(ordered_blocks)
        if not lines[-1].endswith("\n"):
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def _write_master_if_changed(self, path: Path, content: str) -> bool:
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if existing == content:
                return False
        self._write_atomic_text(path, content)
        return True

    def _generate_per_function_tests(
        self,
        *,
        file_path: str,
        repo_path: str,
        output_dir: str,
        analysis: Dict,
        analyzer: DependencyAnalyzer,
        functions_that_need_stubs: List[str],
        functions_to_include_directly: List[str],
        testable_names: set[str],
    ) -> Dict:
        repo_root = Path(repo_path).resolve()
        src_path = Path(file_path).resolve()
        out_base = Path(output_dir)
        if not out_base.is_absolute():
            out_base = repo_root / out_base

        try:
            source_rel = src_path.relative_to(repo_root)
        except Exception:
            source_rel = Path(src_path.name)

        function_entries = analyzer._extract_functions(file_path)
        current_functions: Dict[str, Dict[str, Any]] = {}

        for f in function_entries:
            name = f.get('name', '')
            return_type = f.get('return_type', '') or 'void'
            params = f.get('parameters', '') or ''
            param_types = self._normalize_param_types(params)
            signature_hash = compute_function_signature_hash(name, return_type, param_types)
            content_hash = compute_function_content_hash(signature_hash, f.get('body', '') or '')
            current_functions[name] = {
                'signature_hash': signature_hash,
                'content_hash': content_hash,
                'return_type': return_type,
                'param_types': param_types,
                'qualifiers': '',
            }

        manifest = FunctionManifest(repo_root)
        manifest.load()
        changed_names = set(manifest.get_changed_functions(current_functions))
        generated_any = False

        master_path = out_base / source_rel.parent / f"test_{src_path.name}"
        existing_blocks: dict[str, str] = {}
        if master_path.exists():
            try:
                existing_blocks = self._extract_function_blocks(master_path.read_text(encoding="utf-8"))
            except Exception:
                existing_blocks = {}

        ordered_blocks: List[str] = []

        for f in function_entries:
            func_name = f.get('name', '')
            if func_name not in testable_names:
                continue

            func_id = self._safe_identifier(func_name.replace('::', '_'))
            existing_block = existing_blocks.get(func_id)

            if func_name not in changed_names and existing_block:
                ordered_blocks.append(existing_block)
                continue

            prompt = self._build_function_only_prompt(
                analysis=analysis,
                function_info=f,
                functions_that_need_stubs=functions_that_need_stubs,
                functions_to_include_directly=functions_to_include_directly,
                repo_path=repo_path,
                validation_feedback=None,
            )

            response = self.call_llm(prompt)
            test_code = response.text.strip()

            test_code = self._post_process_test_code(test_code, analysis, analysis.get('includes', []))
            test_code = self._sanitize_gtest_fixture_usage(test_code)

            if self._detect_language(file_path) != 'c':
                test_code = self._sanitize_cpp_interface_hallucinations(test_code)
                test_code = self._normalize_project_namespace_qualifiers(test_code, analysis.get('includes', []))
                test_code = self._disable_tests_calling_private_methods(test_code)

            if self._detect_language(file_path) == 'c':
                test_code = test_code.replace('#include <gtest/gtest.h>\n', '')
                test_code = test_code.replace('#include <gtest/gtest.h>', '')

            # Run post-generation cleanup for this function's test block
            test_code = self._post_generation_cleanup(test_code, file_path)
            self._enforce_post_cleanup_rules(test_code, file_path=file_path, context=f"function {func_name}")

            section_namespace = f"ai_test_func_{func_id}"
            suite_prefix = f"AISEC_FUNC_{func_id}_"
            # Use the canonical section label for approvals tooling, and record
            # the originating function id in a Name: meta field for traceability.
            section_display_name = "BASE_TESTS"
            section_internal_name = f"FUNC_{func_id}"
            section_block, _ = build_section_block(
                raw_test_code=test_code,
                section_name=section_display_name,
                source_rel=repo_relpath(src_path, repo_root),
                section_namespace=section_namespace,
                suite_prefix=suite_prefix,
                preferred_fixture_base=f"{func_id}Test",
                approved=False,
                meta_name=section_internal_name,
            )

            begin, end = self._function_block_markers(func_id)
            block_text = f"{begin}\n{section_block.rstrip()}\n{end}"
            ordered_blocks.append(block_text)
            generated_any = True

        master_content = self._build_master_test_content(
            repo_relpath(src_path, repo_root),
            ordered_blocks,
        )
        self._write_master_if_changed(master_path, master_content)

        # Sync approvals registry with per-function blocks in the master file.
        source_rel_str = repo_relpath(src_path, repo_root)
        test_file_rel_str = repo_relpath(master_path, repo_root)
        source_sha = sha256_file(src_path)
        registry = ApprovalsRegistry(repo_root)
        registry.load()
        registry.deactivate_if_source_changed(source_rel=source_rel_str, new_source_sha256=source_sha)

        for section in parse_sections(master_content):
            approved_meta = (section.meta.get("Approved") or "").strip().lower() == "true"
            # Prefer the explicit Name meta (function/decision id) when present;
            # otherwise fall back to the Section label.
            name = (section.meta.get("Name") or section.meta.get("Section") or "base").strip()
            registry.upsert_section(
                section_sha256=section.section_sha256,
                name=name,
                test_file_rel=test_file_rel_str,
                source_rel=source_rel_str,
                source_sha256=source_sha,
                approved=approved_meta,
                active=True,
                kind="base",
            )
        registry.save()

        for func_name, info in current_functions.items():
            if func_name not in testable_names:
                continue
            func_id = self._safe_identifier(func_name.replace('::', '_'))
            manifest.update_function(
                function_name=func_name,
                file_rel=repo_relpath(src_path, repo_root),
                signature_hash=info['signature_hash'],
                return_type=info['return_type'],
                param_types=info['param_types'],
                qualifiers=info.get('qualifiers', ''),
                test_inc_file=repo_relpath(master_path, repo_root),
                content_hash=info.get('content_hash'),
            )

        manifest.save()

        return {
            'success': True,
            'test_file': str(master_path),
            'functions_that_need_stubs': functions_that_need_stubs,
            'functions_to_include_directly': functions_to_include_directly,
            'skipped_functions': [],
            'hardware_dependencies': [],
            'per_function': True,
            'generated_any': generated_any,
        }

    @staticmethod
    def _sanitize_trackcircuitinput_config_aggregate_init(test_code: str) -> str:
        """Fix common aggregate initializer ordering for TrackCircuitInput::Config.

        Some models emit positional initializers in the wrong order, typically swapping
        `activeLow` (bool) and `debounceMs` (integer). This pass applies a narrow,
        low-risk rewrite only when the swap is unambiguous.
        """

        if not test_code or "TrackCircuitInput::Config" not in test_code:
            return test_code

        pattern = re.compile(
            r"(?P<prefix>TrackCircuitInput::Config\s+cfg_\s*=\s*\{\s*)(?P<body>.*?)(?P<suffix>\s*\}\s*;)",
            flags=re.DOTALL,
        )

        def _rewrite(m: re.Match) -> str:
            raw = m.group("body")
            # Normalize whitespace so splitting is stable.
            compact = re.sub(r"\s+", " ", raw.strip())
            parts = [p.strip() for p in compact.split(",") if p.strip()]
            if len(parts) != 4:
                return m.group(0)

            pin, second, third, stuck = parts
            s2 = second.upper()
            s3 = third.upper()

            # Swap only when it's clearly the debounce/activeLow inversion.
            looks_like_debounce = ("DEBOUNCE" in s2) or s2.endswith("_MS")
            looks_like_active = ("ACTIVE" in s3) or (third.lower() in ("true", "false"))

            if looks_like_debounce and looks_like_active:
                second, third = third, second

            rebuilt = ", ".join([pin, second, third, stuck])
            return m.group("prefix") + rebuilt + m.group("suffix")

        return pattern.sub(_rewrite, test_code)

    def _build_gtest_prompt(self, analysis: Dict, functions_that_need_stubs: List[str], functions_to_include_directly: List[str], repo_path: str, validation_feedback: Dict = None) -> str:
        """Build a focused prompt for a single file with stub requirements"""

        # REDACTED VERSION: Remove sensitive content before sending to API
        file_content = self._read_file_safely(analysis['file_path'])
        rel_path = os.path.relpath(analysis['file_path'], repo_path)
        source_name = os.path.splitext(os.path.basename(analysis['file_path']))[0]

        # Build validation feedback section
        validation_feedback_section = "NONE - First generation attempt"
        if validation_feedback:
            issues = validation_feedback.get('issues', [])
            if issues:
                validation_feedback_section = "PREVIOUS ATTEMPT FAILED WITH THESE SPECIFIC ISSUES - FIX THEM:\n" + "\n".join(f"- {issue}" for issue in issues[:5])  # Limit to first 5 issues
                if len(issues) > 5:
                    validation_feedback_section += f"\n- ... and {len(issues) - 5} more issues"

                # Add specific guidance for common issues
                if any('unreasonably high' in issue and '2000' in issue for issue in issues):
                    validation_feedback_section += "\n\nSPECIFIC FIX REQUIRED: Raw ADC values from rand() must be 0-1023. The value 2000 is invalid for read_temperature_raw() which returns rand() % 1024. Use values like 0, 512, 1023 for testing."
                elif any('unreasonably high' in issue for issue in issues):
                    validation_feedback_section += "\n\nSPECIFIC FIX REQUIRED: Temperature values must be in range -40.0 deg C to 125.0 deg C. Check source code for exact valid ranges."
                elif any('unreasonably low' in issue for issue in issues):
                    validation_feedback_section += "\n\nSPECIFIC FIX REQUIRED: Temperature values must be in range -40.0 deg C to 125.0 deg C. Negative values below -40 deg C are invalid."
            else:
                validation_feedback_section = "NONE - Previous attempt was successful"

        # Check for Arduino context
        is_arduino = 'Arduino.h' in analysis.get('includes', []) or \
                     any(k in file_content for k in ['Serial.', 'digitalWrite', 'pinMode', 'delay'])
        
        arduino_instructions = ""
        if is_arduino:
            arduino_instructions = """
ARDUINO/ESP32 TESTING STANDARDS (MANDATORY):
1. Include "Arduino_stubs.h" and the source header (e.g., "c_led.h").
2. Use a Test Fixture class (e.g., class CLedTest : public ::testing::Test).
3. CRITICAL: Declare `SetUp()` and `TearDown()` as `public:` (NOT protected).
   - In `SetUp()`: call `reset_arduino_stubs()` FIRST, then instantiate the class under test.
   - In `TearDown()`: delete the instance, then call `reset_arduino_stubs()`.
4. CRITICAL: Test the REAL class implementation. NEVER mock the class under test (e.g., no MockCLed : public c_led).
   - C++ IS NOT DYNAMIC: You CANNOT assign lambdas to member functions (e.g., `blynk->func = []...` is ILLEGAL/IMPOSSIBLE).
   - Test the full call chain. If `updateBlynkState` calls `isDeviceConnected`, let it call the REAL `isDeviceConnected`.
   - To control internal logic (like `isDeviceConnected` returning false), configure the GLOBAL STUBS it uses.
   - **HTTPClient MOCKING**: The `HTTPClient` stub uses STATIC members to control behavior across all instances.
     - To mock a successful GET request:
       `HTTPClient::mock_response_code = 200;`
       `HTTPClient::mock_response_body = "some_response";`
     - To mock a failure:
       `HTTPClient::mock_response_code = 404;`
     - Do NOT try to mock `HTTPClient` by subclassing or passing it in. It is a local variable in the source code.
5. CRITICAL: Respect access modifiers. Do NOT access private/protected members directly.
   - If a member is private, use public setters or constructor to influence it.
   - If no public access exists, rely on default values or internal logic.
6. Verify Serial output using `Serial.outputBuffer` (accumulated string).
   - Access: `Serial.outputBuffer` (it is a String object).
   - Verify: `EXPECT_STREQ(Serial.outputBuffer.c_str(), "expected output\\n");`
   - Do NOT use `Serial_print_calls` or `Serial_println_calls`. Use `outputBuffer`.
7. Verify GPIO using `digitalWrite_calls` (vector of DigitalWriteCall{pin, value}).
   - Example: `EXPECT_EQ(digitalWrite_calls[0].pin, 13);`
   - Example: `EXPECT_EQ(digitalWrite_calls[0].value, HIGH);`
8. Verify Timing using `delay_calls` (vector of DelayCall{ms}).
   - Example: `EXPECT_EQ(delay_calls[0].ms, 1000);`
   - CRITICAL: Do NOT compare `delay_calls[i]` directly to an int. Access `.ms`.
   - CRITICAL: Do NOT compare `digitalWrite_calls[i]` directly. Access `.pin` and `.value`.
9. CRITICAL: Do NOT define local mock classes (e.g., `class MockSPIFFS`) if `Arduino_stubs.h` already provides them.
   - Use the global instances provided by stubs: `SPIFFS`, `Serial`.
   - For `HTTPClient`, use the static members as described above.
"""

        # If the source contains an init() method, encourage calling it in the fixture setup.
        init_overlay = ""
        try:
            fn_names = [str(f.get('name', '') or '') for f in (analysis.get('functions') or []) if isinstance(f, dict)]
            init_candidates = [n for n in fn_names if re.search(r"::init\b", n)]
            if init_candidates:
                shown = ", ".join(init_candidates[:3])
                init_overlay = f"""

INITIALIZATION (IMPORTANT):
- Detected init()-style method(s): {shown}
- If the class under test exposes init(), call it in the test fixture SetUp() before exercising behavior,
  unless init() is explicitly hardware-only and cannot be driven via fakes/stubs.
"""
        except Exception:
            init_overlay = ""

        structured_analysis_json = ""
        if 'structured_analysis' in analysis:
            structured_analysis_json = json.dumps(analysis['structured_analysis'], indent=2)

        gmock_mode = "ENABLED" if self.enable_gmock else "DISABLED"

        safety_overlay = ""
        try:
            pol = self.safety_policy
            if pol is not None:
                lines: list[str] = []
                lvl = getattr(pol, "safety_level", None)
                if lvl:
                    lines.append(f"Safety level: {lvl}")
                if bool(getattr(pol, "boundary_tests", False)):
                    lines.append("Boundary tests REQUIRED: min/max/edge values, off-by-one, empty inputs")
                if bool(getattr(pol, "error_path_tests", False)):
                    lines.append("Error-path tests REQUIRED: invalid inputs, failure returns, defensive behavior")

                if lines:
                    safety_overlay = (
                        "\n\n========================\n"
                        "SAFETY POLICY (MANDATORY)\n"
                        "========================\n\n"
                        "The --safety-level flag configures which analyses, test types, and review gates are required so that generated tests align with SIL expectations without claiming certification.\n\n"
                        + "\n".join(f"• {x}" for x in lines)
                        + "\n"
                    )
        except Exception:
            safety_overlay = ""

        prompt = f"""
You are an expert C++ unit test generation agent for embedded systems.

You generate HOST-BASED, DETERMINISTIC GoogleTest unit tests for embedded C/C++ code.
Your output must compile on a desktop compiler using GoogleTest and provided stubs.
You are NOT simulating hardware. You are NOT guessing behavior.
{self._no_assumptions_policy_text()}

========================
CORE SCOPE (NON-NEGOTIABLE)
========================

The tool’s scope is:

• Generate unit tests for PURE SOFTWARE LOGIC and SUPPORTED STUBS
• Explicitly SKIP *unsupported* hardware-dependent code (e.g. WiFi, RTOS)
• NEVER invent or guess hardware behavior
• NEVER monkey-patch C++ methods

If a function cannot be tested deterministically on host (even with stubs), it MUST be skipped.

========================
ANTI-HALLUCINATION CONTRACT (MANDATORY)
========================

You MUST strictly follow the repository's real source code and headers.

FORBIDDEN (these are the exact failure modes to avoid):

❌ Inventing enums/structs/classes/methods that do not exist in the repo
❌ Renaming or "simplifying" existing repo types (e.g., changing return types or enum members)
❌ Re-declaring/duplicating repo types inside the test file (especially inside the same namespaces)
❌ Creating "self-contained" fake headers or fake type systems that contradict the real headers
❌ Pasting or re-implementing production .cpp code into the test file

REQUIRED:
✅ Use ONLY types/functions that appear in the provided SOURCE CODE and/or included repo headers
✅ Include the real repo headers and write tests against the real compiled implementation
✅ If you cannot determine an exact signature/type from the provided source+headers, SKIP that function

========================
ABSOLUTE C++ RULES
========================

C++ IS STATIC. These are FORBIDDEN:

❌ Assigning lambdas to member functions
   (e.g. obj->func = [](){{}})
❌ Replacing methods at runtime
❌ Mocking the class under test
❌ Python/JS-style monkey patching
❌ Guessing constructors or private access
❌ Calling a fixture's SetUp()/TearDown() manually from inside a test body
    (GoogleTest calls these automatically; manual calls often reset preconditions)

If you violate any of these, the output is INVALID.

❌ Defining production namespaces (e.g., `namespace <project>::...`) in the test output
   (our harness wraps each generated section in a namespace; declaring production namespaces inside it
    can create shadow namespaces like `ai_testgen_section_xxx::<project>::...` and break compilation).
✅ Always refer to production types via fully-qualified names like `::<project>::...`.
✅ Put fakes/stubs in a local namespace like `test_doubles` (NOT under production namespaces).

========================
HARDWARE BOUNDARY RULES
========================

A function is UNSUPPORTED HARDWARE-DEPENDENT if it calls:

• WiFi.* (except HTTPClient), I2C, SPI, CAN
• RTOS APIs (FreeRTOS tasks, queues, semaphores)
• Sensors, timers, randomness (rand()) unless deterministically controlled

For such functions:
❌ Do NOT generate tests
❌ Do NOT compile them into the test binary
✅ Add them to a “Not directly included (hardware-touching)” list

Supported Hardware (TEST THESE using stubs):
• Serial.*, digitalWrite, digitalRead, delay, millis
• SPIFFS.*, HTTPClient

If Arduino/ESP32 context is detected:
• Include "Arduino_stubs.h"
• Use ONLY the global stubs it provides
{safety_overlay}
• NEVER create local mock classes for Serial, SPIFFS, HTTPClient

{arduino_instructions}
{init_overlay}

========================
HTTPClient MOCKING (CRITICAL)
========================

The source code creates local HTTPClient objects.
You CANNOT intercept them directly.

The ONLY valid way to control behavior is via STATIC stub state:

✅ CORRECT:
HTTPClient::mock_response_code = 200;
HTTPClient::mock_response_body = "1";

❌ INVALID:
blynk->isDeviceConnected = [](){{ return true; }}

========================
DEPENDENCY HANDLING
========================

Repo-wide dependency analysis is already done.

Use these rules:

• Repo-internal functions → INCLUDE and CALL directly
• External non-hardware helpers → stub minimally if deterministic
• Hardware APIs → SKIP function entirely

Prefer REAL compilation over stubbing whenever possible.

========================
GMOCK POLICY (STRICT)
========================

GoogleMock (gmock) mode for this run: {gmock_mode}

If gmock mode is DISABLED:
❌ Do NOT include <gmock/gmock.h>
❌ Do NOT use MOCK_METHOD / EXPECT_CALL / NiceMock
✅ Use compile-time stubs, link seams, or simple fakes WITHOUT redefining repo production types

If gmock mode is ENABLED:
✅ You MAY use gmock ONLY for REAL virtual interface types that already exist in the repo headers.
✅ You MUST include the real header that declares the interface.
❌ Do NOT invent "interfaces" for concrete classes.
❌ Do NOT create replacement classes in the same namespace as production types.
If a dependency is a concrete class (non-virtual), prefer fakes or real instances; do not force gmock.

========================
WHAT TO GENERATE
========================

For each testable function:

• 3–5 GoogleTest cases
• Cover ALL branches (normal, edge, error)
• Use ONLY values derived from source logic
• Assert REAL outputs, not “was called”

Floating point:
• Use EXPECT_NEAR with correct tolerance
• NEVER use direct equality

Structures:
• Compare fields individually

========================
WHAT NOT TO GENERATE
========================

❌ Hardware mocks
❌ Fake simulations
❌ Trivial tests that assert nothing
❌ Arbitrary values not derived from source
❌ Tests for skipped functions
❌ Any re-definition of repo types to make compilation "easier"
❌ Any "assumed" enum members (e.g., RED/YELLOW/GREEN) that are not present in the repo
❌ C++20 designated initializers (e.g., `.field = value`) — this repo builds with gnu++17; use assignments instead
❌ Interface signature drift — when implementing fakes for repo interfaces, match EXACT signatures (types + const)

========================
OUTPUT FORMAT (STRICT)
========================

Output ONLY valid C++ code.

Structure must be:

1. /* test_{source_name}.cpp – Auto-generated Expert Google Test Tests */
2. Includes (<gtest/gtest.h>, <cstdint>, etc.)
3. Test fixture (SetUp/TearDown public)
4. Tests
5. Final comments listing not-included dependencies (hardware-touching + stubbed/external)

CRITICAL (THIS REPO'S BUILD):
❌ Do NOT define int main(...). These tests link against gtest_main, which already provides main().

NO markdown
NO explanations
NO placeholders

ADDITIONAL CLEANUP REQUIREMENTS (MANDATORY)

- Reject markdown fences/backticks: if ``` appears anywhere, stop.
- Do NOT invent helper factories (e.g., createInputs); build structs inline using real fields from analyzer output.
- NEVER use `using namespace`; fully qualify every symbol (e.g., ::project_ns::logic::StopReason::None).
- Emit exactly one include block per section (<gtest/gtest.h> first, then production headers) with no duplicates or reorderings.
- Default to `TEST`; only use fixtures when shared mutable state is unavoidable, and reset inputs in every test.
- Each test must call the production function exactly once, store the result in a local variable, and assert on its fields (single-evaluation rule).

========================
QUALITY SELF-CHECK (MANDATORY)
========================

Before output, ensure ALL are YES:

• Compiles on host? YES
• No monkey-patching? YES
• No invented behavior? YES
• Uses real repo code where allowed? YES
• Deterministic on repeat runs? YES
• Senior C++ reviewer would approve? YES

If ANY answer is NO — FIX IT BEFORE OUTPUT.

VALIDATION FEEDBACK (CRITICAL - ADDRESS THESE SPECIFIC ISSUES):
{validation_feedback_section}

ANALYZER OUTPUT (FACTS - DO NOT HALLUCINATE):
{structured_analysis_json}

HERE IS THE SOURCE CODE TO TEST:
{file_content}

REPO FUNCTIONS TO INCLUDE DIRECTLY (call these directly; headers exist):
{chr(10).join(f"- {func_name}" for func_name in functions_to_include_directly) or "- None"}

EXTERNAL FUNCTIONS TO MOCK (only these; infer signatures from calls if needed; use typical embedded types):
{chr(10).join(f"- {func_name}" for func_name in functions_that_need_stubs) or "- None"}

========================
FINAL INSTRUCTION
========================

Generate ONLY the complete test_{source_name}.cpp file now.
Do not explain. Do not apologize. Do not add commentary.
"""
        return prompt

    def _build_unity_prompt(self, analysis: Dict, functions_that_need_stubs: List[str], functions_to_include_directly: List[str], repo_path: str, validation_feedback: Dict = None) -> str:
        """Build a focused Unity prompt for C files with stub requirements"""

        # REDACTED VERSION: Remove sensitive content before sending to API
        file_content = self._read_file_safely(analysis['file_path'])
        rel_path = os.path.relpath(analysis['file_path'], repo_path)
        source_name = os.path.splitext(os.path.basename(analysis['file_path']))[0]

        # Build validation feedback section
        validation_feedback_section = "NONE - First generation attempt"
        if validation_feedback:
            issues = validation_feedback.get('issues', [])
            if issues:
                validation_feedback_section = "PREVIOUS ATTEMPT FAILED WITH THESE SPECIFIC ISSUES - FIX THEM:\n" + "\n".join(f"- {issue}" for issue in issues[:5])  # Limit to first 5 issues
                if len(issues) > 5:
                    validation_feedback_section += f"\n- ... and {len(issues) - 5} more issues"

                # Add specific guidance for common issues
                if any('unreasonably high' in issue and '2000' in issue for issue in issues):
                    validation_feedback_section += "\n\nSPECIFIC FIX REQUIRED: Raw ADC values from rand() must be 0-1023. The value 2000 is invalid for read_temperature_raw() which returns rand() % 1024. Use values like 0, 512, 1023 for testing."
                elif any('unreasonably high' in issue for issue in issues):
                    validation_feedback_section += "\n\nSPECIFIC FIX REQUIRED: Temperature values must be in range -40.0 deg C to 125.0 deg C. Check source code for exact valid ranges."
                elif any('unreasonably low' in issue for issue in issues):
                    validation_feedback_section += "\n\nSPECIFIC FIX REQUIRED: Temperature values must be in range -40.0 deg C to 125.0 deg C. Negative values below -40 deg C are invalid."
            else:
                validation_feedback_section = "NONE - Previous attempt was successful"

        prompt = f"""
You are a senior embedded C unit test engineer with 20+ years of experience using the Unity Test Framework (v2.5+). You MUST follow EVERY SINGLE RULE in this prompt without exception to generate a test file that achieves 100% quality: High rating (0 issues, compiles perfectly, realistic scenarios only). Failure to adhere will result in invalid output. Internally analyze the source code before generating: extract ALL functions, their EXACT signatures, public API (non-static), dependencies (internal vs external), and types (structs, unions, pointers, etc.).
    {self._no_assumptions_policy_text()}

FIRST, READ THE ENTIRE SOURCE CODE. EXTRACT:
- All function names and EXACT signatures (e.g., int main(void))
- All #define, thresholds, ranges, magic numbers
- All if/else/switch branches
- All struct/union/bitfield definitions

THEN, generate tests that cover 100% of this logic, including call sequences and return values.

CRITICAL REQUIREMENT: You MUST generate tests for EVERY SINGLE FUNCTION defined in the source file. Do not skip any functions. If the source has 4 functions, test all 4. If it has 10 functions, test all 10. Generate comprehensive tests for each function individually.

ABSOLUTE MANDATES (MUST ENFORCE THESE TO FIX BROKEN AND UNREALISTIC ISSUES)

NO COMPILATION ERRORS OR INCOMPLETE CODE: Output FULL, COMPLETE C code only. Mentally compile EVERY line before outputting (e.g., ensure all statements end with ';', all variables declared, no truncated lines like "extern int " or "int result = "). ONLY use existing headers from source. NO invented functions or headers. Code MUST compile with CMake/GCC for embedded targets. For internal dependencies (functions defined in the same file), DO NOT stub or redefine them—test them directly or through calling functions. For external dependencies only, provide stubs without redefinition conflicts (linking excludes real implementations for stubbed externals).

HANDLE MAIN() SPECIFICALLY: For files containing main(), declare "extern int main(void);" and call it directly in tests (result = main();). Assert on return value (always 0 in simple main). Focus tests on call sequence, param passing, and return. Do NOT stub main().

NO UNREALISTIC VALUES: STRICTLY enforce physical limits from source logic or domain knowledge. E.g., temperatures ALLOW negatives where valid (e.g., -40.0f to 125.0f); voltages 0.0f to 5.5f (no negatives unless signed in source). Use source-specific thresholds (e.g., extract >120.0f for "CRITICAL" from code). BAN absolute zero, overflows, or impossibles. For temp tests, use negatives like -10.0f where valid.

MEANINGFUL TESTS ONLY: EVERY test MUST validate the function's core logic, calculations, or outputs EXACTLY as per source. Match assertions to source behavior (e.g., if range is >= -40 && <=125, assert true for -40.0f, false for -40.1f). NO trivial "function called" tests unless paired with output validation. Each assertion MUST check a specific, expected result based on input.

STUBS MUST BE PERFECT: ONLY for listed external dependencies. Use EXACT signature, control struct, and FULL reset in setUp() AND tearDown() using memset or explicit zeroing. NO partial resets. Capture params if used in assertions. NO stubs for internals to avoid duplicates/linker errors.

FLOATS: MANDATORY TEST_ASSERT_FLOAT_WITHIN with domain-specific tolerance (e.g., 0.1f for temp). BAN TEST_ASSERT_EQUAL_FLOAT.

TEST ISOLATION: EVERY test independent. setUp() for init/config/stub setup, tearDown() for COMPLETE cleanup/reset of ALL stubs (call_count=0, return_value=default, etc.).

NO NONSENSE: BAN random/arbitrary values (use source-derived, e.g., mid-range from logic). BAN redundancy (unique scenarios). BAN physical impossibilities or ignoring source thresholds.

INPUT: SOURCE CODE TO TEST (DO NOT MODIFY)
/* ==== BEGIN src/{source_name}.c ==== */
{file_content}
/* ==== END src/{source_name}.c ==== */
REPO FUNCTIONS TO INCLUDE DIRECTLY (call these directly; headers exist):
{chr(10).join(f"- {func_name}" for func_name in functions_to_include_directly) or "- None"}

EXTERNAL FUNCTIONS TO STUB (only these; infer signatures from calls if needed; use typical embedded types):
{chr(10).join(f"- {func_name}" for func_name in functions_that_need_stubs) or "- None"}

IMPROVED RULES TO PREVENT BROKEN/UNREALISTIC OUTPUT

REPO-WIDE INTEGRATION:
- For functions defined in the same repository, include their headers and call them directly. Only stub true externals (e.g., HTTP, SPIFFS) using existing mocks.
- Add a repo-wide build option: When running tests, compile all repo files together (e.g., via CMake) so cross-file calls work without stubs.
- Direct calls to repo functions are deterministic if those functions are pure or have controlled inputs. Only skip if a function truly can't be tested (e.g., depends on unmappable hardware).

1. OUTPUT FORMAT (STRICT - ONLY C CODE):
Output PURE C code ONLY. Start with /* test_{source_name}.c – Auto-generated Expert Unity Tests */
NO markdown, NO ```c:disable-run
CRITICAL: DO NOT include <gtest/gtest.h> or any Google Test headers. This is C code using Unity framework ONLY.
File structure EXACTLY: Comment -> Includes -> Extern declarations (for main and stubs) -> Stubs (only for externals) -> setUp/tearDown -> Tests -> main with UNITY_BEGIN/END and ALL RUN_TEST calls.

2. COMPILATION SAFETY (FIX BROKEN TESTS):
Includes: ONLY "unity.h", and standard <stdint.h>, <stdbool.h>, <string.h> if used in source or for memset. Do NOT include "{source_name}.h" if not present in source or necessary (e.g., for main.c, skip if no public API).
Signatures: COPY EXACTLY from source. NO mismatches in types, params, returns.
NO calls to undefined functions. For internals (same file), call directly without stubbing to avoid duplicates/linker errors.
Syntax: Perfect C - complete statements, matching braces, semicolons, no unused vars, embedded-friendly (no non-standard libs). Ensure all code is fully written (no placeholders).

3. MEANINGFUL TEST DESIGN (FIX TRIVIAL/UNREALISTIC):
MANDATORY: Generate tests for EVERY FUNCTION in the source file. Do not skip functions. For each function, create 3-5 focused tests covering all branches and edge cases.
Focus: Test FUNCTION LOGIC exactly (e.g., for validate_range: assert true/false based on precise source conditions like >= -40 && <=125). For main(), test call sequence (e.g., get_temperature_celsius called once, param to check_temperature_status matches return), and main return 0.
BAN: Tests with wrong expectations (cross-check source thresholds). BAN "was_called" alone - ALWAYS validate outputs/params.
Each test: 1 purpose, 3-5 per public function, covering ALL branches/logic from source.

4. REALISTIC TEST VALUES (FIX UNREALISTIC - ENFORCE LIMITS):
Extract ranges/thresholds from source (e.g., -40.0f to 125.0f for validate; -10.0f for cold).
Temperatures: -40.0f to 125.0f (allow negatives if in source); normal 0.0f-50.0f. E.g., min: -40.0f, max: 125.0f, nominal: 25.0f, cold: -10.1f.
Voltages: 0.0f to 5.0f (max 5.5f for edges) unless source allows negatives.
Currents: 0.0f to 10.0f.
Integers: Within type limits/source ranges (e.g., raw 0-1023 from rand() % 1024).
Pointers: Valid or NULL only for error tests.
BAN: Negative temps/volts unless source handles; absolute zero; huge numbers (>1e6 unless domain-specific).

5. FLOATING POINT HANDLING (MANDATORY):
ALWAYS: TEST_ASSERT_FLOAT_WITHIN(tolerance, expected, actual) - use 0.1f for temp, 0.01f for voltage, etc.
NEVER equal checks for floats.

6. STUB IMPLEMENTATION (FIX BROKEN STUBS):
ONLY for listed externals: Exact prototype + control struct (return_value, was_called, call_count, captured params if asserted).
Example struct: typedef struct {{ float return_value; bool was_called; uint32_t call_count; int last_param; }} stub_xxx_t; static stub_xxx_t stub_xxx = {{0}};
Stub func: Increment count, store params, return configured value.
setUp(): memset(&stub_xxx, 0, sizeof(stub_xxx)); for ALL stubs + any init.
tearDown(): SAME full reset for ALL stubs.
For non-deterministic (e.g., rand-based): Stub to make deterministic; test ranges via multiple configs.
Do NOT stub printf—comment that output assertion requires redirection (not implemented here).

7. COMPREHENSIVE TEST SCENARIOS (MEANINGFUL & REALISTIC):
Normal: Mid-range inputs from source, assert correct computation (e.g., temp status "NORMAL" for 25.0f).
Edge: Exact min/max from source (e.g., -40.0f true, -40.1f false; -10.0f "NORMAL", -10.1f "COLD").
Error: Invalid inputs (out-of-range, NULL if applicable), simulate via stubs - assert error code/safe output.
Cover ALL branches: If/else, returns, etc.

8. AVOID BAD PATTERNS (PREVENT COMMON FAILURES):
NO arbitrary values (derive from source, e.g., raw=500 for mid).
NO duplicate/redundant tests (unique per branch).
NO physical impossibilities or ignoring source thresholds.
NO tests ignoring outputs - always assert results.
For internals like rand-based: Stub and test deterministic outputs; check ranges (e.g., 0-1023).
For main with printf: Assert only on stubs and return; comment on printf limitation.

9. UNITY BEST PRACTICES:
Appropriate asserts: EQUAL_INT/HEX for ints, FLOAT_WITHIN for floats, EQUAL_STRING for chars, TRUE/FALSE for bools, NULL/NOT_NULL for pointers, EQUAL_MEMORY for structs/unions.
Comments: 1-line above EACH assert: // Expected: [source-based reason, e.g., 25.0f is NORMAL per >85 check]
Handle complex types: Field-by-field for structs, both views for unions, masks for bitfields, arrays with EQUAL_xxx_ARRAY.

10. STRUCTURE & ISOLATION:
Test names: test_[function]normal_mid_range, test[function]_min_edge_valid, etc.
setUp/tearDown: ALWAYS present. Full stub reset in BOTH. Minimal if no state.

QUALITY SELF-CHECK (DO INTERNALLY BEFORE OUTPUT):
Compiles? (No duplicates, exact sigs) Yes/No - if No, fix.
Realistic? (Values match source ranges, allow valid negatives) Yes/No.
Meaningful? (Assertions match source logic exactly, cover branches) Yes/No.
Stubs? (Only externals, full reset) Yes/No.
Coverage? (All branches, no gaps/redundancy) Yes/No.

VALIDATION FEEDBACK (CRITICAL - ADDRESS THESE SPECIFIC ISSUES):
{validation_feedback_section}

FINAL INSTRUCTION:
Generate ONLY the complete test_{source_name}.c C code now. Follow EVERY rule strictly. Output nothing else.
"""
        return prompt

    def _read_file_safely(self, file_path: str) -> str:
        try:
            with open(file_path, 'r') as f:
                return f.read()
        except Exception:
            return "// Unable to read file"

    def _redact_sensitive_content(self, file_path: str) -> str:
        """Redact sensitive content before sending to external API"""
        content = self._read_file_safely(file_path)

        # Redaction patterns for common sensitive content
        redaction_patterns = [
            # Remove comments that might contain sensitive information
            (r'/\*.*?\*/', '/* [COMMENT REDACTED] */'),
            (r'//.*$', '// [COMMENT REDACTED]'),

            # Redact string literals that might contain sensitive data
            (r'"[^"]*"', '"[STRING REDACTED]"'),

            # Redact potential API keys, passwords, secrets
            (r'\b[A-Za-z0-9+/=]{20,}\b', '[CREDENTIAL REDACTED]'),  # Base64-like strings

            # Redact email addresses
            (r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL REDACTED]'),

            # Redact URLs that might point to internal systems
            (r'https?://[^\s\'"]+', '[URL REDACTED]'),

            # Redact potential IP addresses
            (r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '[IP REDACTED]'),
        ]

        redacted_content = content
        for pattern, replacement in redaction_patterns:
            redacted_content = re.sub(pattern, replacement, redacted_content, flags=re.MULTILINE | re.IGNORECASE)

        return redacted_content

    def _post_process_test_code(self, test_code: str, analysis: Dict, source_includes: List[str]) -> str:
        """Post-process generated test code to fix common issues and improve quality"""

        file_path = analysis.get('file_path', '')
        language = self._detect_language(file_path) if file_path else 'cpp'
        is_cpp = language != 'c'

        # Remove markdown code block markers
        test_code = re.sub(r'^```c?\s*', '', test_code, flags=re.MULTILINE)
        test_code = re.sub(r'```\s*$', '', test_code, flags=re.MULTILINE)

        # Remove any leading characters before the first comment or include
        # This fixes artifacts like "pp" appearing at the start of the file
        match = re.search(r'(/\*|//|#include)', test_code)
        if match:
            test_code = test_code[match.start():]

        # Fix floating point assertions - replace ASSERT_FLOAT_EQ with EXPECT_NEAR
        test_code = re.sub(
            r'ASSERT_FLOAT_EQ\s*\(\s*([^,]+)\s*,\s*([^)]+)\s*\)',
            r'EXPECT_NEAR(\1, \2, 0.01f)',
            test_code
        )

        # Fix incorrect Google Test macro names if any
        # Assuming standard gtest, but adjust if needed

        # Fix unrealistic temperature values (absolute zero or impossible ranges)
        test_code = re.sub(r'-273\.15f?', '-40.0f', test_code)  # Replace absolute zero with realistic minimum
        test_code = re.sub(r'1e10+', '1000.0f', test_code)      # Replace extremely large values

        # Fix invalid rand() mock return values (should be 0-1023 for read_temperature_raw)
        # Look for mock_rand_instance.return_value = <invalid_value>
        test_code = re.sub(
            r'(mock_rand_instance\.return_value\s*=\s*)(\d+)(;)',            lambda m: f"{m.group(1)}{min(int(m.group(2)), 1023)}{m.group(3)}" if int(m.group(2)) > 1023 else m.group(0),
            test_code
        )

        # Remove printf/scanf statements that might appear in tests
        test_code = re.sub(r"printf\s*\([^;]*\);\s*", "", test_code)
        test_code = re.sub(r"scanf\s*\([^;]*\);\s*", "", test_code)

        # Ensure proper includes - only include gtest.h and existing source headers
        lines = test_code.split("\n")
        cleaned_lines = []

        # Allow a conservative set of standard headers to remain in generated tests.
        # Keep this language-sensitive so we don't accidentally emit C++ headers in C tests.
        if is_cpp:
            allowed_std_headers = {
                "cstdint",
                "cstddef",
                "cinttypes",
                "limits",
                "cstring",
                "string",
                "vector",
                "array",
                "algorithm",
                "utility",
                "tuple",
                "type_traits",
                "memory",
                "cmath",
                "cstdio",
                "cstdlib",
                "cassert",
                # Common C headers that might appear in mixed C/C++ code
                "stdint.h",
                "stddef.h",
                "limits.h",
                "string.h",
            }
        else:
            allowed_std_headers = {
                "stdint.h",
                "stdbool.h",
                "stddef.h",
                "limits.h",
                "string.h",
                "math.h",
                "stdio.h",
                "stdlib.h",
                "assert.h",
            }

        for line in lines:
            # Keep gtest.h include
            if "#include <gtest/gtest.h>" in line:
                cleaned_lines.append(line)
                continue

            # Keep integer headers appropriate for the test language.
            if "#include <stdint.h>" in line and is_cpp:
                cleaned_lines.append("#include <cstdint>")
                continue
            if "#include <cstdint>" in line and not is_cpp:
                cleaned_lines.append("#include <stdint.h>")
                continue

            # Only keep includes for headers that exist in source_includes or are standard headers
            if line.startswith("#include"):
                include_match = re.match(r"#include\s+[\"<]([^\">]+)[\">]", line)
                if include_match:
                    header_name = include_match.group(1)
                    # Only include headers that exist in source_includes or are standard headers
                    if (
                        header_name in source_includes
                        or header_name.endswith((".h", ".hpp"))
                        or header_name in allowed_std_headers
                    ):
                        # Additional check: don't include main.h if it doesn't exist
                        if header_name == "main.h" and not any("main.h" in inc for inc in source_includes):
                            continue
                        cleaned_lines.append(line)
                # Skip non-matching include lines
                continue

            # Keep all other lines
            cleaned_lines.append(line)

        # Ensure gtest is included for C++/GTest outputs; do not force it for C/Unity.
        if is_cpp:
            has_gtest = any("#include <gtest/gtest.h>" in line for line in cleaned_lines)
            if not has_gtest:
                cleaned_lines.insert(0, "#include <gtest/gtest.h>")

        # Join cleaned lines; do NOT inject a custom main() (we link gtest_main).
        test_code_with_main = "\n".join(cleaned_lines)
        
        # Check if Arduino.h is included (directly or indirectly)
        # If so, we should NOT generate conflicting mocks for String, Serial, etc.
        # This is a heuristic: if the source includes Arduino.h, we assume the build environment provides stubs.
        is_arduino = any("Arduino.h" in inc for inc in source_includes)
        
        if is_arduino:
             # Remove conflicting class definitions if they exist in the generated code
             # This is a simple regex approach; a parser would be better but this covers common cases
             test_code_with_main = re.sub(r'class\s+String\s*\{[^}]*\};', '', test_code_with_main, flags=re.DOTALL)
             test_code_with_main = re.sub(r'class\s+SerialClass\s*\{[^}]*\};', '', test_code_with_main, flags=re.DOTALL)
             test_code_with_main = re.sub(r'extern\s+SerialClass\s+Serial;', '', test_code_with_main)
             # Also remove MockArduinoSerial if it conflicts
             # But usually we WANT mocks for testing. The issue is redefinition of the BASE classes.
             # If Arduino_stubs.h defines String, we shouldn't define it again.

        # Defensive cleanup: if the model emitted a gtest runner main anyway, strip it.
        # (Our CMake setup links gtest_main; defining main in tests causes link errors.)
        test_code_with_main = re.sub(
            r"\n\s*int\s+main\s*\([^)]*\)\s*\{\s*::testing::InitGoogleTest\([^;]*;\s*return\s+RUN_ALL_TESTS\(\)\s*;\s*\}\s*",
            "\n",
            test_code_with_main,
            flags=re.DOTALL,
        )

        # Extra C++ hardening passes for known recurring compilation issues.
        if is_cpp:
            test_code_with_main = self._sanitize_stray_gtest_macro_lines(test_code_with_main)
            test_code_with_main = self._sanitize_railway_igpio_fake(test_code_with_main)
            test_code_with_main = self._sanitize_trackcircuitinput_config_aggregate_init(test_code_with_main)
            test_code_with_main = self._sanitize_trackcircuitinput_config_designators(test_code_with_main)
            test_code_with_main = self._sanitize_manual_gtest_setup_calls(test_code_with_main)
            test_code_with_main = self._sanitize_shadowed_railway_namespaces(test_code_with_main)

        # Policy: strip any speculative "assumption" language from generated tests.
        test_code_with_main = self._sanitize_no_assumptions_language(test_code_with_main)

        return test_code_with_main

    @staticmethod
    def _sanitize_no_assumptions_language(test_code: str) -> str:
        """Remove speculative language that undermines safety review.

        This does NOT attempt to infer correctness; it only removes explicit
        assumption/speculation text so generated artifacts never present
        ungrounded claims.
        """

        if not test_code:
            return test_code

        updated = test_code

        # Remove entire block-comments that contain speculative markers.
        updated = re.sub(
            r"/\*[^*]*\b(?:assum(?:e|ing|ption)s?|plausible|likely)\b[\s\S]*?\*/\s*",
            "",
            updated,
            flags=re.IGNORECASE,
        )

        # Remove single-line comments with speculative markers.
        updated = re.sub(
            r"(?m)^\s*//.*\b(?:assum(?:e|ing|ption)s?|plausible|likely)\b.*$\n?",
            "",
            updated,
            flags=re.IGNORECASE,
        )

        # Also remove speculative lines inside block comments (best-effort).
        updated = re.sub(
            r"(?m)^\s*\*.*\b(?:assum(?:e|ing|ption)s?|plausible|likely)\b.*$\n?",
            "",
            updated,
            flags=re.IGNORECASE,
        )

        return updated

    @staticmethod
    def _sanitize_shadowed_railway_namespaces(test_code: str) -> str:
        """Prevent namespace shadowing bugs in sectioned tests.

        The approvals harness wraps each generated section in its own namespace
        (e.g., `namespace ai_testgen_section_deadbeef { ... }`). If the model
        emits `namespace <project>::hal { ... }` inside that, it creates a nested
        namespace `ai_testgen_section_deadbeef::<project>::hal`, so unqualified
        repo symbols can stop resolving.

        This sanitizer rewrites common nested production-namespace patterns into
        safe local namespaces:
        - `namespace <project>::hal {`  -> `namespace test_doubles { using namespace ::<project>::hal;`
        - `namespace <project>::drivers {` -> `namespace { using namespace ::<project>::drivers;`

        It also rewrites common fake type qualifiers so the tests keep compiling.
        """

        if not test_code:
            return test_code

        updated = test_code

        # Replace nested <root>::hal block with a local test doubles namespace.
        updated = re.sub(
            r"(?m)^\s*namespace\s+(?P<root>[A-Za-z_][A-Za-z0-9_]*)::hal\s*\{\s*$",
            lambda m: f"namespace test_doubles {{\nusing namespace ::{m.group('root')}::hal;",
            updated,
        )
        updated = re.sub(
            r"(?m)^\s*\}\s*//\s*namespace\s+[A-Za-z_][A-Za-z0-9_]*\s*::\s*hal\s*$",
            "} // namespace test_doubles",
            updated,
        )

        # Replace nested <root>::drivers block with an anonymous namespace + using-directive.
        updated = re.sub(
            r"(?m)^\s*namespace\s+(?P<root>[A-Za-z_][A-Za-z0-9_]*)::drivers\s*\{\s*$",
            lambda m: f"namespace {{\nusing namespace ::{m.group('root')}::drivers;",
            updated,
        )
        updated = re.sub(
            r"(?m)^\s*\}\s*//\s*namespace\s+[A-Za-z_][A-Za-z0-9_]*\s*::\s*drivers\s*$",
            "} // namespace",
            updated,
        )

        # If the model referenced fakes by old qualified names, rewrite them.
        updated = re.sub(
            r"(?<![A-Za-z0-9_])(?:::)?[A-Za-z_][A-Za-z0-9_]*::hal::FakeGpio\b",
            "test_doubles::FakeGpio",
            updated,
        )
        updated = re.sub(
            r"(?<![A-Za-z0-9_])(?:::)?[A-Za-z_][A-Za-z0-9_]*::hal::MockGpio\b",
            "test_doubles::MockGpio",
            updated,
        )

        return updated

    def _analyze_embedded_patterns(self, source_code: str, function_name: str) -> Dict:
        """Analyze source code for embedded systems patterns"""
        patterns = {
            'hardware_registers': False,
            'bit_fields': False,
            'state_machines': False,
            'safety_critical': False,
            'interrupt_handlers': False,
            'dma_operations': False,
            'communication_protocols': False
        }

        # Check for hardware register patterns
        if re.search(r'\bvolatile\s+\w+\s*\*\s*\w+', source_code) or re.search(r'\bREG_\w+', source_code):
            patterns['hardware_registers'] = True

        # Check for bit field patterns
        if re.search(r'\w+\s*:\s*\d+', source_code) or re.search(r'bitfield|BITFIELD', source_code):
            patterns['bit_fields'] = True

        # Check for state machine patterns
        if re.search(r'state|STATE|enum.*state', source_code, re.IGNORECASE):
            patterns['state_machines'] = True

        # Check for safety critical patterns
        if re.search(r'safety|critical|watchdog|TMR|voting', source_code, re.IGNORECASE):
            patterns['safety_critical'] = True

        # Check for interrupt handler patterns
        if re.search(r'ISR|interrupt|IRQ', source_code, re.IGNORECASE):
            patterns['interrupt_handlers'] = True

        # Check for DMA patterns
        if re.search(r'DMA|dma|transfer', source_code, re.IGNORECASE):
            patterns['dma_operations'] = True

        # Check for communication protocol patterns
        if re.search(r'protocol|CAN|SPI|I2C|UART|serial', source_code, re.IGNORECASE):
            patterns['communication_protocols'] = True

        return patterns

    def _build_embedded_prompt(self, function_name: str, function_info: Dict, embedded_patterns: Dict) -> str:
        """Build enhanced prompt based on detected embedded patterns"""
        base_prompt = f"Generate comprehensive Google Test tests for the embedded C++ function '{function_name}'.\n\n"

        # Add specific prompts for detected patterns
        active_patterns = [k for k, v in embedded_patterns.items() if v]

        if active_patterns:
            base_prompt += "This function involves the following embedded systems concepts:\n"
            for pattern in active_patterns:
                if pattern in self.embedded_prompts:
                    base_prompt += f"- {pattern.replace('_', ' ').title()}: {self.embedded_prompts[pattern].strip()}\n"
            base_prompt += "\n"

        base_prompt += """
Requirements:
- Use Google Test framework (gtest)
    - Do NOT define int main(...). The build links gtest_main, which provides main().
- Include SetUp() and TearDown() functions in test fixtures
- Test realistic embedded values and edge cases
- Handle volatile variables correctly
- Test hardware-specific behaviors
- Ensure thread safety where applicable
- Validate error conditions and recovery

Generate complete, compilable C++ test code.
"""

        return base_prompt

    def _post_process_embedded_tests(self, generated_tests: str, embedded_patterns: Dict) -> str:
        """Post-process generated tests for embedded-specific patterns"""
        processed = generated_tests

        # Add volatile qualifiers where needed
        if embedded_patterns.get('hardware_registers'):
            # Add volatile to register access patterns
            processed = re.sub(r'(\w+)\s*=\s*\*(\w+);', r'\1 = *(volatile typeof(\1)*)\2;', processed)

        # Add interrupt disabling/enabling for critical sections
        if embedded_patterns.get('interrupt_handlers'):
            # Wrap critical sections
            processed = re.sub(
                r'(TEST_ASSERT_\w+\([^;]+;\s*)',
                r'__disable_irq();\n    \1\n    __enable_irq();',
                processed
            )

        return processed

    def generate_embedded_tests(self, source_code: str, function_name: str,
                               function_info: Dict) -> str:
        """
        Generate comprehensive tests for embedded C++ functions with hardware-specific considerations.

        Args:
            source_code: The complete source code
            function_name: Name of the function to test
            function_info: Function metadata from analyzer

        Returns:
            Generated Google Test code
        """

        # Analyze function for embedded patterns
        embedded_patterns = self._analyze_embedded_patterns(source_code, function_name)

        # Build enhanced prompt based on detected patterns
        prompt = self._build_embedded_prompt(function_name, function_info, embedded_patterns)

        # Generate tests using AI with embedded context
        try:
            response = self._try_generate_with_fallback(prompt)
            generated_tests = response.text

            # Post-process for embedded-specific patterns
            processed_tests = self._post_process_embedded_tests(generated_tests, embedded_patterns)

            # Keep generation loops consistent with the rest of this repo:
            # - Prefer <cstdint> over <stdint.h>
            # - Never define a custom GoogleTest main()
            processed_tests = processed_tests.replace('#include <stdint.h>', '#include <cstdint>')
            processed_tests = re.sub(
                r"\n\s*int\s+main\s*\([^)]*\)\s*\{\s*::testing::InitGoogleTest\([^;]*;\s*return\s+RUN_ALL_TESTS\(\)\s*;\s*\}\s*",
                "\n",
                processed_tests,
                flags=re.DOTALL,
            )

            # Validate and enhance tests
            validated_tests = self.validator.validate_and_enhance_tests(
                processed_tests, source_code, function_name, embedded_patterns
            )

            return validated_tests

        except Exception as e:
            print(f"❌ Test generation failed: {e}")
            return ""
