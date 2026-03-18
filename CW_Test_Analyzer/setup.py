from setuptools import setup, find_packages

with open("requirements.txt") as f:
    requirements = f.read().splitlines()

setup(
    name="ai-c-test-analyzer",
    version="0.1.0",
    packages=find_packages(),
    install_requires=requirements,
    entry_points={
        'console_scripts': [
            'ai-c-test-analyzer=ai_c_test_analyzer.cli:main',
            'ai-test-analyzer=ai_c_test_analyzer.cli:main',
        ],
    },
)