from setuptools import setup, find_packages

setup(
    name="ai-c-test-cov",
    version="0.1.0",
    packages=find_packages(),
    install_requires=["gcovr>=7.2"],
    entry_points={"console_scripts": ["ai-c-testcov=ai_c_test_coverage.cli:main"]},
)
