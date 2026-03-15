from setuptools import setup, find_packages

setup(
    name="gdrive_client",
    version="0.1.0",
    description="Minimal Google Drive Linux Client",
    packages=find_packages(),
    py_modules=["main"],
    install_requires=[
        "google-api-python-client",
        "google-auth-oauthlib",
        "watchdog",
    ],
    entry_points={
        "console_scripts": [
            "gdrive-client=main:main",
        ],
    },
)
