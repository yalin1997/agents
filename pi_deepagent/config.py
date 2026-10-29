"""Load local CLI configuration without overriding the process environment."""

from pathlib import Path

from dotenv import load_dotenv


def load_environment() -> None:
    """Read only the .env in the current working directory."""
    load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
