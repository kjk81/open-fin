"""Unit tests for the ``extract_tickers`` utility in agent/nodes.py.

Covers @TICKER, $TICKER, bare uppercase, stopword filtering, deduplication,
and edge-case inputs.
"""

from __future__ import annotations

import pytest


class TestExtractTickersAtPrefix:
    """@TICKER mention style — highest priority."""

    def test_single_at_ticker(self) -> None:
        from agent.nodes import extract_tickers

        assert extract_tickers("@RBLX") == ["RBLX"]

    def test_at_ticker_in_sentence(self) -> None:
        from agent.nodes import extract_tickers

        result = extract_tickers("How is @RBLX doing today?")
        assert result == ["RBLX"]

    def test_at_ticker_lowercase_normalized(self) -> None:
        from agent.nodes import extract_tickers

        # Mixed-case @-prefix is now supported; normalised to uppercase
        result = extract_tickers("@rblx")
        assert result == ["RBLX"]

    def test_at_ticker_uppercase_still_works(self) -> None:
        from agent.nodes import extract_tickers

        result = extract_tickers("@RBLX")
        assert result == ["RBLX"]

    def test_multiple_at_tickers(self) -> None:
        from agent.nodes import extract_tickers

        result = extract_tickers("Compare @RBLX and @NVDA performance")
        assert "RBLX" in result
        assert "NVDA" in result


class TestExtractTickersDollarPrefix:
    """$TICKER mention style — second priority."""

    def test_single_dollar_ticker(self) -> None:
        from agent.nodes import extract_tickers

        assert extract_tickers("$AAPL") == ["AAPL"]

    def test_dollar_ticker_in_sentence(self) -> None:
        from agent.nodes import extract_tickers

        result = extract_tickers("I'm watching $TSLA closely")
        assert "TSLA" in result

    def test_multiple_dollar_tickers(self) -> None:
        from agent.nodes import extract_tickers

        result = extract_tickers("$AAPL and $GOOG both rallied")
        assert "AAPL" in result
        assert "GOOG" in result


class TestExtractTickersBareUppercase:
    """Bare uppercase fallback — lowest priority."""

    def test_single_bare_ticker(self) -> None:
        from agent.nodes import extract_tickers

        result = extract_tickers("TSLA is up today")
        assert "TSLA" in result

    def test_bare_ticker_isolated(self) -> None:
        from agent.nodes import extract_tickers

        assert extract_tickers("TSLA") == ["TSLA"]


class TestExtractTickersMixed:
    """Mixed prefix styles in a single message."""

    def test_at_and_dollar(self) -> None:
        from agent.nodes import extract_tickers

        result = extract_tickers("@RBLX and $AAPL")
        assert "RBLX" in result
        assert "AAPL" in result
        assert len(result) == 2

    def test_all_three_styles(self) -> None:
        from agent.nodes import extract_tickers

        result = extract_tickers("@RBLX $AAPL TSLA all look interesting")
        assert "RBLX" in result
        assert "AAPL" in result
        assert "TSLA" in result

    def test_mixed_with_stopwords(self) -> None:
        from agent.nodes import extract_tickers

        result = extract_tickers("I think @MSFT and $GOOG are good picks")
        assert "MSFT" in result
        assert "GOOG" in result
        assert "I" not in result


class TestExtractTickersDeduplication:
    """Same ticker via different prefixes must appear only once."""

    def test_at_dollar_bare_same_symbol(self) -> None:
        from agent.nodes import extract_tickers

        result = extract_tickers("@AAPL $AAPL AAPL")
        assert result == ["AAPL"]

    def test_dollar_bare_same_symbol(self) -> None:
        from agent.nodes import extract_tickers

        result = extract_tickers("$TSLA TSLA")
        assert result == ["TSLA"]

    def test_order_preserved(self) -> None:
        from agent.nodes import extract_tickers

        result = extract_tickers("@RBLX and $AAPL")
        # @ comes before $, so RBLX should precede AAPL
        assert result.index("RBLX") < result.index("AAPL")


class TestExtractTickersStopwords:
    """Common English words and macro abbreviations must be filtered."""

    def test_common_stopwords(self) -> None:
        from agent.nodes import extract_tickers

        result = extract_tickers("I AM an AI that tracks stocks")
        assert result == []

    def test_financial_abbreviations(self) -> None:
        from agent.nodes import extract_tickers

        result = extract_tickers("The SEC and FED raised CPI above GDP")
        assert result == []

    def test_corporate_suffixes(self) -> None:
        from agent.nodes import extract_tickers

        result = extract_tickers("The company is an INC LLC")
        assert result == []

    def test_stopword_and_real_ticker(self) -> None:
        from agent.nodes import extract_tickers

        result = extract_tickers("I AM watching MSFT and AI stocks")
        assert "MSFT" in result
        assert "I" not in result
        assert "AM" not in result
        assert "AI" not in result


class TestExtractTickersEdgeCases:
    """Boundary conditions and unusual inputs."""

    def test_empty_string(self) -> None:
        from agent.nodes import extract_tickers

        assert extract_tickers("") == []

    def test_lowercase_only(self) -> None:
        from agent.nodes import extract_tickers

        assert extract_tickers("invalid lowercase text") == []

    def test_numbers_only(self) -> None:
        from agent.nodes import extract_tickers

        assert extract_tickers("12345 6789") == []

    def test_six_char_symbol_ignored(self) -> None:
        """Tickers are max 5 chars — TOOLONG should not match."""
        from agent.nodes import extract_tickers

        result = extract_tickers("TOOLONG is not valid")
        assert "TOOLONG" not in result

    def test_at_prefix_six_chars_ignored(self) -> None:
        from agent.nodes import extract_tickers

        result = extract_tickers("@TOOLONG is invalid")
        assert "TOOLONG" not in result

    def test_single_char_ticker(self) -> None:
        """Single uppercase letters that are not stopwords should match."""
        from agent.nodes import extract_tickers

        # 'F' (Ford) is a real 1-char ticker and not in stopwords
        result = extract_tickers("$F looks cheap")
        assert "F" in result


class TestExtractTickersLowercaseNormalization:
    """Mixed-case @/$ prefixes must all normalise to uppercase."""

    def test_lowercase_at_prefix_normalized(self) -> None:
        from agent.nodes import extract_tickers

        # Lowercase @-prefix — should normalise to RBLX
        result = extract_tickers("@rblx")
        assert result == ["RBLX"]

    def test_mixedcase_at_prefix_normalized(self) -> None:
        from agent.nodes import extract_tickers

        result = extract_tickers("@RbLx")
        assert result == ["RBLX"]

    def test_lowercase_dollar_prefix_normalized(self) -> None:
        from agent.nodes import extract_tickers

        result = extract_tickers("$aapl")
        assert result == ["AAPL"]

    def test_mixedcase_dollar_prefix_normalized(self) -> None:
        from agent.nodes import extract_tickers

        result = extract_tickers("$Tsla")
        assert result == ["TSLA"]

    def test_at_casing_deduplication(self) -> None:
        """Three casings of the same @-ticker reduce to one result."""
        from agent.nodes import extract_tickers

        result = extract_tickers("@RBLX @rblx @RblX")
        assert result == ["RBLX"]

    def test_dollar_casing_deduplication(self) -> None:
        from agent.nodes import extract_tickers

        result = extract_tickers("$AAPL $aapl $Aapl")
        assert result == ["AAPL"]

    def test_lowercase_at_then_bare_deduplication(self) -> None:
        """@rblx → RBLX; subsequent bare RBLX must not produce a second entry."""
        from agent.nodes import extract_tickers

        result = extract_tickers("@rblx then RBLX")
        assert result == ["RBLX"]

    def test_at_and_dollar_lowercases_deduplicated(self) -> None:
        """Same ticker via @lowercase and $lowercase is deduplicated."""
        from agent.nodes import extract_tickers

        result = extract_tickers("@msft $msft")
        assert result == ["MSFT"]
