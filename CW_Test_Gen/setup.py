"""
Setup script for AI C Test Generator
"""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="ai-c-test-generator",
    version="1.0.0",
    author="AI C Test Generator Team",
    author_email="testgen@example.com",
    description="AI-powered C and C++ test generator",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/your-org/ai-c-test-generator",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3.8",
    ],
    python_requires=">=3.8",
    install_requires=[
        "google-genai",
    ],
    entry_points={
        "console_scripts": [
            "ai-c-testgen=ai_c_test_generator.cli:main",
            "ai-test-gen=ai_c_test_generator.cli:main",
        ],
    },
)
