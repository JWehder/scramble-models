from setuptools import setup, find_packages

setup(
    name="scramble_models",
    version="0.1.0",
    description="Shared models and logic for Scramble App",
    author="Your Name",
    packages=find_packages(include=["models", "models.*"]),
    install_requires=[
        "pymongo==4.6.1",
        "pydantic",
        "boto3",
        "dotenv",
        "bcrypt",
        "email_validator",
        "flask-mailman"
    ],
    python_requires=">=3.8",
)