import pytest

from kya.face import template as ft

from .conftest import outcome

LIVE = {"passed": True, "reason": "turned as asked"}
PHOTO = {"passed": False, "reason": "no head movement (static image)"}


@pytest.fixture
def pending(world):
    out = world.call("pay_merchant", merchant_id="m_freshmart", amount_inr=4500)
    assert outcome(out) == ("STEP_UP", "L8")
    return out["challenge"]["challenge_id"]


def enrolled(world):
    return ft.from_blob(world.store.user("u_demo_001")["face_template"])


def test_live_same_person_approves_and_executes(world, pending):
    res = world.bank.resolve_stepup(pending, liveness=LIVE, embedding=ft.near(enrolled(world), 0.72))
    assert res["outcome"] == "ALLOW"
    assert res["result"]["status"] == "paid"
    assert world.store.balance("ACC-0001") == 85_000 - 4500


def test_photo_fails_liveness(world, pending):
    res = world.bank.resolve_stepup(pending, liveness=PHOTO, embedding=enrolled(world))
    assert res["outcome"] == "BLOCK" and res["reason"].startswith("liveness failed")
    assert world.store.balance("ACC-0001") == 85_000


def test_different_person_fails_match(world, pending):
    res = world.bank.resolve_stepup(pending, liveness=LIVE, embedding=ft.near(enrolled(world), 0.10))
    assert res["outcome"] == "BLOCK" and "does not match" in res["reason"]


def test_human_can_decline(world, pending):
    assert world.bank.resolve_stepup(pending, deny=True)["outcome"] == "BLOCK"


def test_challenge_is_single_use(world, pending):
    world.bank.resolve_stepup(pending, liveness=LIVE, embedding=enrolled(world))
    again = world.bank.resolve_stepup(pending, liveness=LIVE, embedding=enrolled(world))
    assert again["outcome"] == "BLOCK"
    assert world.store.balance("ACC-0001") == 85_000 - 4500  # paid exactly once


def test_swapped_template_breaks_mandate_binding(world, pending):
    other = ft.synthetic(4242)
    world.store.exec("UPDATE users SET face_template=? WHERE user_id='u_demo_001'", (ft.to_blob(other),))
    res = world.bank.resolve_stepup(pending, liveness=LIVE, embedding=other)
    assert res["outcome"] == "BLOCK" and "committed in the mandate" in res["reason"]


def test_timeout(world, clock, pending):
    clock.t += 600
    res = world.bank.resolve_stepup(pending, liveness=LIVE, embedding=enrolled(world))
    assert res["reason"] == "step-up timed out"


def test_three_failures_revoke_mandate(world, clock):
    for _ in range(3):
        clock.t += 86_400
        out = world.call("pay_merchant", merchant_id="m_freshmart", amount_inr=4500)
        res = world.bank.resolve_stepup(out["challenge"]["challenge_id"], liveness=PHOTO, embedding=None)
    assert res.get("mandate_revoked")
    assert outcome(world.call("get_balance")) == ("BLOCK", "L4")
