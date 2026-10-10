"""Escrow integrity (D21 assisted holds): escrow may never go negative, and conservation_ok()
must not report a balanced ledger that hides a negative escrow (credit minted from nothing)."""
import pytest

import passiveworkers.ledger as L

ESCROW_ID = L.ESCROW_ID


def _err():
    # resolved at call time: other test modules importlib.reload(passiveworkers.ledger), which
    # rebinds the exception class — a module-level import would hold a stale one.
    return L.InsufficientCredit


def _ledger():
    led = L.Ledger()
    led.open_account("alice", grant_amount=100.0)
    led.open_account("bob", grant_amount=100.0)
    return led


def test_release_more_than_held_is_refused_and_mutates_nothing():
    led = _ledger()
    led.hold("alice", 30.0)
    with pytest.raises(_err()):
        led.release("bob", 50.0)
    assert led.balance(ESCROW_ID) == 30.0
    assert led.balance("bob") == 100.0
    assert led.conservation_ok()


def test_refund_more_than_held_is_refused_and_mutates_nothing():
    led = _ledger()
    led.hold("alice", 30.0)
    with pytest.raises(_err()):
        led.refund("alice", 31.0)
    assert led.balance(ESCROW_ID) == 30.0
    assert led.balance("alice") == 70.0
    assert led.conservation_ok()


def test_double_payout_of_one_hold_is_refused():
    # the F1 shape at ledger level: refund the hold, then try to also release it.
    led = _ledger()
    led.hold("alice", 50.0)
    led.refund("alice", 50.0)
    with pytest.raises(_err()):
        led.release("bob", 50.0)
    assert led.balance(ESCROW_ID) == 0.0 and led.balance("bob") == 100.0
    assert led.conservation_ok()


def test_exact_hold_releases_to_zero():
    led = _ledger()
    led.hold("alice", 12.3456)
    led.release("bob", 12.3456)
    assert led.balance(ESCROW_ID) == 0.0
    assert led.conservation_ok()


def test_conservation_flags_negative_escrow_even_when_sum_balances():
    led = _ledger()
    led._escrow().balance = -50.0            # what the pre-fix double-pay produced …
    led.accounts["bob"].balance += 50.0      # … with the minted 50 sitting in bob's account
    assert abs(led.total_credit() - 200.0) < 1e-9   # the sum alone still "balances"
    assert not led.conservation_ok()
