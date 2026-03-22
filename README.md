*VERISAFE**

VERISAFE is an AI-powered Python framework for automated unit test generation, coverage analysis, and safety-oriented verification workflows for C/C++ projects.

It is designed to accelerate validation in safety-critical environments by enabling rapid onboarding, modular extensibility, and seamless integration with both demo and production codebases.


**Key Features**

AI-assisted unit test generation for C/C++ codebases

Code coverage analysis with per-file HTML reports

Modular architecture designed for multi-agent workflows

Demo-ready setup with a sample RailwaySignalSystem project

One-command bootstrap with minimal configuration

Scalable design for integration into validation pipelines


**Architecture Overview**

VERISAFE is structured into independent modules:

CW_Test_Analyzer - Project code analysis

CW_Test_Gen – Test generation engine

CW_Test_Cov – Coverage analysis and reporting

CW_Test_Run – Test execution orchestration

This modular design enables extensibility and future integration with CI/CD pipelines and automotive validation toolchains.

**Installation**

Clone the repository and set up a virtual environment:

git clone https://github.com/SwathantraPulicherla/VERISAFE.git
cd VERISAFE

python -m venv .venv

Activate the environment:

Windows:

.\.venv\Scripts\activate

Linux/macOS:

source .venv/bin/activate

Install dependencies:

pip install --upgrade pip
pip install -r CW_Test_Cov/requirements.txt -r CW_Test_Gen/requirements.txt -r CW_Test_Run/requirements.txt
Usage

**Run the interactive demo:**

python run_demo.py --repo-path <path-to-your-cpp-repo>

**Example using the included demo project:**

python run_demo.py --repo-path RailwaySignalSystem

Follow the on-screen menu to generate, review, and execute tests, and to view coverage reports.

**Capabilities**

Automatic unit test generation

Test execution and result analysis

Coverage reporting with HTML outputs

Support for both demo and external C/C++ repositories

**Use Cases**

Validation of safety-critical embedded software

Automotive ECU unit testing workflows

Rapid onboarding for legacy C/C++ codebases

AI-assisted verification pipelines

**Roadmap**

Integration with CI/CD pipelines

Support for automotive validation tools such as CANoe

Enhanced AI-driven test optimization

Extended multi-agent orchestration capabilities

**Contribution**

Contributions are welcome. Please feel free to fork the repository, raise issues, or submit pull requests.

**Author**

Swathantra Pulicherla
Senior Systems Engineer – Automotive Systems and Embedded Software
