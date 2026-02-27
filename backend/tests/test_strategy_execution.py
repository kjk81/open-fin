import pytest

from worker import StrategyOutput, run_strategy_subprocess


def test_strategy_output_schema_valid():
    item = StrategyOutput.model_validate(
        {
            "action": "BUY",
            "ticker": "AAPL",
            "qty": 10,
            "confidence": 0.9,
        }
    )
    assert item.action == "BUY"
    assert item.ticker == "AAPL"


def test_strategy_output_schema_invalid_ticker():
    with pytest.raises(Exception):
        StrategyOutput.model_validate(
            {
                "action": "SELL",
                "ticker": "aapl",
                "qty": 1,
                "confidence": 0.5,
            }
        )


def test_unknown_strategy_raises():
    with pytest.raises(ValueError):
        run_strategy_subprocess("does_not_exist", "AAPL", {})
