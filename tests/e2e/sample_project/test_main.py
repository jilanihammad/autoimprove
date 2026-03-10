"""Tests for sample project — 3 passing, 1 skipped."""

import pytest
from main import calculate_stats, process_data
from utils import proc, fmt, chk


def test_calculate_stats_basic():
    result = calculate_stats([10, 20, 30])
    assert result["count"] == 3
    assert result["total"] == 60
    assert result["avg"] == 20.0


def test_calculate_stats_empty():
    result = calculate_stats([])
    assert result["count"] == 0
    assert result["avg"] == 0


def test_fmt():
    assert fmt(["a", "b", "c"]) == "a,b,c"
    assert fmt([1, 2], sep="-") == "1-2"


@pytest.mark.skip(reason="Not implemented yet")
def test_process_data_with_file():
    pass
