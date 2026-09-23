from datetime import datetime

import pytest

from kya.agent.attacks import ATTACKS
from kya.mandate import DAY
from kya.risk import IST

from .conftest import outcome


def test_benign_payment_allowed_and_settled(world):
    out = world.call("pay_merchant", merchant_id="m_freshmart", amount_inr=1200)
    assert outcome(out) == ("ALLOW", None)
    assert out["result"]["status"] == "paid"
    assert world.store.balance("ACC-0001") == 85_000 - 1200


def test_read_only_action_allowed(world):
    out = world.call("get_balance")
    assert outcome(out) == ("ALLOW", None)
    assert out["result"]["balance_inr"] == 85_000


@pytest.mark.parametrize("attack_id", list(ATTACKS))
def test_every_attack_is_stopped_at_its_layer(world, attack_id):
    attack = ATTACKS[attack_id]
    final = attack.run(world)[-1]
    assert final["decision"]["outcome"] in ("BLOCK", "STEP_UP")
    assert final["decision"]["layer"] == attack.expected_layer
    assert "result" not in final  # nothing executed


def test_blocked_payment_moves_no_money(world):
    ATTACKS["injection_amount"].run(world)
    assert world.store.balance("ACC-0001") == 85_000


def test_step_up_above_afa_style_limit(world):
    out = world.call("pay_merchant", merchant_id="m_freshmart", amount_inr=4500)
    assert outcome(out) == ("STEP_UP", "L8")
    assert out["challenge"]["direction"] in ("LEFT", "RIGHT")
    assert world.store.balance("ACC-0001") == 85_000


def test_step_up_new_merchant_unusual_amount(world):
    assert outcome(world.call("pay_merchant", merchant_id="m_organic", amount_inr=2900)) == ("STEP_UP", "L8")


def test_ordinary_amount_at_new_merchant_allowed(world):
    assert outcome(world.call("pay_merchant", merchant_id="m_organic", amount_inr=900)) == ("ALLOW", None)


def test_odd_hour_plus_unusual_amount_steps_up(world, clock):
    clock.t = int(datetime(2026, 9, 24, 3, 10, tzinfo=IST).timestamp())
    assert outcome(world.call("pay_merchant", merchant_id="m_freshmart", amount_inr=2900)) == ("STEP_UP", "L8")


def test_cumulative_cap(world, clock):
    for _ in range(6):  # 6 x 2,400 = 14,400, one per day so velocity never triggers
        clock.t += DAY
        assert outcome(world.call("pay_merchant", merchant_id="m_freshmart", amount_inr=2400)) == ("ALLOW", None)
    clock.t += DAY
    out = world.call("pay_merchant", merchant_id="m_freshmart", amount_inr=1000)
    assert outcome(out) == ("BLOCK", "L7")
    assert "30-day cap" in out["decision"]["reason"]


def test_agent_cannot_poison_its_own_baseline(world, clock):
    for _ in range(4):  # the agent makes "unusual" payments look routine... or tries to
        clock.t += DAY
        world.call("pay_merchant", merchant_id="m_freshmart", amount_inr=2900)
    clock.t += DAY
    risk = world.call("pay_merchant", merchant_id="m_freshmart", amount_inr=2900)["decision"]["risk"]
    unusual = next(f for f in risk["features"] if f["name"] == "unusual_amount")
    assert unusual["hit"]  # still judged against the human's own history (median ≈ ₹942)


def test_unknown_merchant_blocked(world):
    assert outcome(world.call("pay_merchant", merchant_id="m_nope", amount_inr=100)) == ("BLOCK", "L7")


@pytest.mark.parametrize("amount", [0, -5, 10.5, "100", True])
def test_bad_amounts_blocked(world, amount):
    assert outcome(world.call("pay_merchant", merchant_id="m_freshmart", amount_inr=amount)) == ("BLOCK", "L7")


@pytest.mark.parametrize("env", [None, "hello", {}, {"v": 1},
                                 {"v": 2, "mandate": {}, "request": {}, "agent_sig": ""}])
def test_malformed_envelopes(world, env):
    assert outcome(world.bank.handle(env)) == ("BLOCK", "L1")


def test_revoke_takes_effect_immediately(world):
    assert outcome(world.call("get_balance"))[0] == "ALLOW"
    world.bank.revoke(world.wallet.mandate["mandate_id"])
    assert outcome(world.call("get_balance")) == ("BLOCK", "L4")


def test_consent_withdrawal_revokes_and_erases_biometrics(world):
    world.bank.withdraw_consent(world.wallet.mandate["mandate_id"])
    out = world.call("get_balance")
    assert outcome(out) == ("BLOCK", "L4")
    assert "consent withdrawn" in out["decision"]["reason"]
    assert world.store.user("u_demo_001")["face_template"] is None
