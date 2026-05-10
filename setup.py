# shared-libraries/setup.py
from setuptools import setup, find_packages

setup(
    name="shared-libraries",
    version="1.0.0",
    packages=find_packages(),
    install_requires=[
        "openai>=1.30.0",
        "httpx>=0.27.0",
        "redis>=5.0.1",
        "python-dotenv>=1.0.0",
    ],
    extras_require={
        "anthropic": ["anthropic>=0.28.0"],
        "google":    ["google-generativeai>=0.7.0"],
        "test":      ["pytest>=8.0.0", "pytest-asyncio>=0.23.0", "pytest-cov>=5.0.0"],
    },
    python_requires=">=3.11",
)
