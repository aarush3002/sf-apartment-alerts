import gzip
import pathlib

import pytest

FIX = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture():
    """Load a saved page. Fixtures are stored gzipped to keep the repo small."""
    def _load(name: str) -> str:
        plain = FIX / name
        if plain.exists():
            return plain.read_text(errors="ignore")
        gz = FIX / (name + ".gz")
        if gz.exists():
            with gzip.open(gz, "rt", errors="ignore") as fh:
                return fh.read()
        raise FileNotFoundError(f"no fixture {name} in {FIX}")
    return _load
