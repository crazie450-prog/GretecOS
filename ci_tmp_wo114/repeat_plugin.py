"""Scratch: --rep N runs each selected test N times (as pytest-repeat does)."""
import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--rep", type=int, default=1)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    n = metafunc.config.getoption("rep")
    if n > 1:
        metafunc.fixturenames.append("_rep_i")
        metafunc.parametrize("_rep_i", range(n))
