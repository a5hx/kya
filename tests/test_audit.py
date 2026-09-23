from kya.agent.attacks import ATTACKS


def busy(world):
    world.call("pay_merchant", merchant_id="m_freshmart", amount_inr=1200)
    ATTACKS["injection_amount"].run(world)
    ATTACKS["replay"].run(world)
    return world.bank.audit


def test_chain_valid_after_activity(world):
    v = busy(world).verify()
    assert v["valid"] and v["length"] >= 5 and v["checkpoint"]["ok"]


def test_decisions_are_logged(world):
    kinds = [e["kind"] for e in busy(world).entries()]
    assert kinds.count("decision") == 4


def test_edit_detected_at_that_entry(world):
    audit = busy(world)
    audit.tamper_edit(3)
    v = audit.verify()
    assert not v["valid"] and v["first_broken"] == 3 and "edited" in v["reason"]


def test_edit_with_rehash_detected_at_next_link(world):
    audit = busy(world)
    audit.tamper_edit(3, rehash=True)
    v = audit.verify()
    assert not v["valid"] and v["first_broken"] == 4


def test_rehashing_the_head_is_caught_by_signed_checkpoint(world):
    audit = busy(world)
    audit.tamper_edit(audit.verify()["length"], rehash=True)
    v = audit.verify()
    assert not v["valid"] and not v["checkpoint"]["ok"]


def test_deletion_detected(world):
    audit = busy(world)
    audit.tamper_delete(2)
    v = audit.verify()
    assert not v["valid"] and "deleted" in v["reason"]


def test_truncation_caught_by_signed_checkpoint(world):
    audit = busy(world)
    audit.tamper_truncate(2)
    v = audit.verify()
    assert not v["valid"] and "truncated" in v["checkpoint"]["detail"]
