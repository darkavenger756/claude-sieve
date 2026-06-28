"""Setup script for Claude-Sieve."""

import os
from setuptools import setup, find_packages


HERE = os.path.abspath(os.path.dirname(__file__))


def read_file(filename: str) -> str:
    path = os.path.join(HERE, filename)
    with open(path, encoding="utf-8") as f:
        return f.read()


setup(
    name="claude-sieve",
    version="3.0.0",
    description="AST-aware test output compressor for LLM agents — "
                "mitigates token inflation and context-window degradation.",
    long_description=read_file("README.md"),
    long_description_content_type="text/markdown",
    author="Claude-Sieve Contributors",
    url="https://github.com/darkavenger756/claude-sieve",
    license="MIT",
    packages=find_packages(exclude=["tests", "tests.*"]),
    include_package_data=True,
    package_data={"claude_sieve": ["py.typed"]},
    python_requires=">=3.10",
    install_requires=[],
    extras_require={
        "dev": [
            "pytest>=7.0",
            "mypy>=1.0",
            "ruff>=0.1",
        ],
        "treesitter": [
            "tree-sitter>=0.22",
        ],
    },
    entry_points={
        "console_scripts": [
            "clavesieve=claude_sieve.main:main",
            "claude-sieve=claude_sieve.main:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Software Development :: Testing",
        "Topic :: Utilities",
        "Environment :: Console",
        "Operating System :: POSIX :: Linux",
        "Operating System :: MacOS :: MacOS X",
    ],
)
