"""Frozen external behavioral checks for the W02 code delivery case."""

import pytest

from finance.orders import total


def test_discount_reduces_total():
    assert total([{"price": 10, "quantity": 2}], discount=5) == 15


def test_discount_clamps_at_zero():
    assert total([{"price": 2, "quantity": 1}], discount=5) == 0


def test_negative_discount_is_rejected():
    with pytest.raises(ValueError):
        total([{"price": 2, "quantity": 1}], discount=-1)


def test_original_behavior_remains():
    assert total([{"price": 2, "quantity": 3}]) == 6

