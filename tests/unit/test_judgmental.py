"""Tests for ig_reel_downloader.judgmental."""

from __future__ import annotations

from unittest.mock import patch

from ig_reel_downloader import judgmental


class TestShouldFire:
    def test_returns_false_when_chance_is_zero(self) -> None:
        assert judgmental.should_fire(0.0, ["url.gif"]) is False

    def test_returns_false_when_chance_is_negative(self) -> None:
        assert judgmental.should_fire(-0.5, ["url.gif"]) is False

    def test_returns_false_when_gif_list_is_empty(self) -> None:
        assert judgmental.should_fire(0.5, []) is False

    def test_returns_false_when_random_above_chance(self) -> None:
        with patch.object(judgmental.random, "random", return_value=0.2):
            assert judgmental.should_fire(0.1, ["url.gif"]) is False

    def test_returns_true_when_random_below_chance(self) -> None:
        with patch.object(judgmental.random, "random", return_value=0.05):
            assert judgmental.should_fire(0.1, ["url.gif"]) is True


class TestPickGif:
    def test_returns_item_from_list(self) -> None:
        gifs = ["a.gif", "b.gif", "c.gif"]
        result = judgmental.pick_gif(gifs)
        assert result in gifs

    def test_returns_only_item_when_list_has_one(self) -> None:
        assert judgmental.pick_gif(["only.gif"]) == "only.gif"
