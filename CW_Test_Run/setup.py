"""
Setup script for AI Test Runner
"""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="ai-test-runner",
    version="1.0.0",
    author="AI Test Runner Team",
    author_email="testrunner@example.com",
    description="Compiles, executes, and provides coverage for AI-generated C and C++ unit tests",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/your-org/ai-test-runner",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Topic :: Software Development :: Testing",
        "Topic :: Software Development :: Build Tools",
    ],
    python_requires=">=3.8",
    install_requires=[],
    entry_points={
        "console_scripts": [
            "ai-test-runner=ai_test_runner.cli:main",
        ],
    },
    keywords="c testing cpp unit-tests ai coverage cmake build execution arduino",
    project_urls={
        "Bug Reports": "https://github.com/your-org/ai-test-runner/issues",
        "Source": "https://github.com/your-org/ai-test-runner",
    },
)
