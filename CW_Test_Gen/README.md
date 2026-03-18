# AI C/C++ Test Generator

AI-powered unit test generator for C and C++ code using Google Gemini AI. Automatically generates comprehensive Google Test framework tests for your functions with intelligent stub generation and validation.

## Features

- 🤖 **AI-Powered**: Uses Google Gemini 2.5 Flash for intelligent test generation
- 🔍 **Smart Analysis**: Automatically analyzes C code dependencies and function relationships
- 🧪 **Unity Framework**: Generates tests compatible with the Unity testing framework
- ✅ **Validation**: Comprehensive test validation and quality assessment
- 📊 **Reports**: Detailed validation reports with compilation status and quality metrics
- 🛠️ **CLI Tool**: Easy-to-use command-line interface
- 📦 **Pip Installable**: Install via pip for global usage

## Installation

### From PyPI (Recommended)
```bash
pip install ai-c-test-generator
```

### From Source
```bash
git clone https://github.com/your-org/ai-c-test-generator.git
cd ai-c-test-generator
pip install -e .
```

## Requirements

- Python 3.8+
- Google Gemini API key
- Unity testing framework (automatically included in generated tests)

## Quick Start

1. **Get a Gemini API Key**
   - Visit [Google AI Studio](https://makersuite.google.com/app/apikey)
   - Create a new API key

2. **Set Environment Variable**
```bash
export GEMINI_API_KEY=your_api_key_here
```

3. **Generate Tests**
```bash
# Navigate to your C project
cd your-c-project

# Generate tests for all C files in src/
ai-c-testgen

# Or specify custom paths
ai-c-testgen --repo-path /path/to/project --source-dir src --output tests
```

## Usage

### Basic Usage
```bash
ai-c-testgen [OPTIONS]
```

### Command Line Options

- `--repo-path PATH`: Path to the C repository (default: current directory)
- `--output DIR`: Output directory for generated tests (default: tests)
- `--api-key KEY`: Google Gemini API key (can also use GEMINI_API_KEY env var)
- `--source-dir DIR`: Source directory containing C files (default: src)
- `--regenerate-on-low-quality`: Automatically regenerate tests that are validated as low quality
- `--max-regeneration-attempts N`: Maximum number of regeneration attempts (default: 2)
- `--quality-threshold LEVEL`: Minimum acceptable quality threshold (**high**/medium/low, default: **high**)
- `--verbose, -v`: Enable verbose output
- `--version`: Show version information
 - `--wait-before-exit`: Wait for user input before exiting to preserve terminal output (useful for GUI or CI tasks)
 - `--log-file PATH`: Path to a file where CLI output will be logged (relative to repo root by default)

### Examples

**Generate tests for current directory:**
```bash
ai-c-testgen
```

**Generate tests with custom paths:**
```bash
ai-c-testgen --repo-path /home/user/my-c-project --source-dir source --output test_output
```

**Use API key directly:**
```bash
ai-c-testgen --api-key YOUR_API_KEY_HERE
```

**Generate tests with strict quality requirements:**
```bash
ai-c-testgen --repo-path /path/to/c/project --regenerate-on-low-quality
```

## Project Structure

Your C project should follow this structure:
```
your-project/
├── src/                    # Your C source files
│   ├── main.c
│   ├── utils.c
│   └── sensors.c
├── tests/                  # Generated tests (created automatically)
│   ├── test_main.c
│   ├── test_utils.c
│   └── compilation_report/  # Validation reports
└── unity/                  # Unity framework (if not present)
```

... (README continues)

## Keep Terminal Output After Run

If you're running the CLI from a GUI or tasks and want to retain the terminal output after the process exits, use either:

- The `--wait-before-exit` option to pause the CLI and keep the terminal visible until you press Enter:
```bash
ai-c-testgen --repo-path /path/to/project --wait-before-exit
```

- Or `--log-file` to write the run output to a file which you can inspect later:
```bash
ai-c-testgen --repo-path /path/to/project --log-file tests/run.log
```

If you're using VS Code tasks to run the CLI, ensure the task's `presentation` block does not clear the terminal, e.g.:
```json
"presentation": {
   "reveal": "always",
   "panel": "shared",
   "clear": false
}
```

Another option when launching PowerShell from another process is to use the `-NoExit` flag:
```powershell
powershell -NoExit -Command "ai-c-testgen --repo-path ."
```