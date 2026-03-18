# AI C Test Analyzer

AI-powered C/C++ code analyzer for unit testing. Analyzes code dependencies, function relationships, and hardware interactions to guide test generation.

## Features

- **Dependency Analysis**: Maps function calls, includes, and relationships
- **Hardware Detection**: Identifies hardware-touching vs pure logic functions
- **Class Classification**: Categorizes classes as LOGIC, HARDWARE, or MIXED
- **Excel Export**: Export analysis results to Excel for easy visualization and sharing
- **JSON Output**: Machine-readable format for integration with other tools

## Installation

```bash
pip install ai-c-test-analyzer
```

## Usage

### Basic Analysis
```bash
ai-c-test-analyzer --repo-path /path/to/c/project
```

### With Excel Export
```bash
ai-c-test-analyzer --repo-path /path/to/c/project --excel-output
```

### Options
- `--repo-path`: Path to the C/C++ repository (required)
- `--verbose`: Enable verbose output
- `--excel-output`: Export results to Excel format
- `--wait-before-exit`: Wait for user input before exiting

## Output

The analyzer creates a `tests/analysis/` directory with:

- `analysis.json`: Complete analysis results in JSON format
- `analysis.xlsx`: Excel spreadsheet with multiple sheets (when `--excel-output` is used)
- Text files: Human-readable summaries of functions, hardware functions, etc.

### Excel Sheets

1. **Function Index**: All functions with their files, calls, hardware flags, and call depths
2. **Call Graph**: Function call relationships (caller → callees)
3. **File Summaries**: File-level analysis with testability and dependency info
4. **Class Roles**: Class classifications (LOGIC/HARDWARE/MIXED)

## Requirements

- Python 3.8+
- openpyxl (for Excel export)