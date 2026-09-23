import copy

from kya import crypto
from kya.mandate import verify_signature


def test_sign_verify_roundtrip():
    sk = crypto.generate_key()
    sig = crypto.sign(sk, {"b": 2, "a": [1, "₹"]})
    assert crypto.verify(crypto.pubkey(sk), {"a": [1, "₹"], "b": 2}, sig)  # key order is irrelevant


def test_any_change_breaks_signature():
    sk = crypto.generate_key()
    sig = crypto.sign(sk, {"cap": 5000})
    assert not crypto.verify(crypto.pubkey(sk), {"cap": 50000}, sig)
    assert not crypto.verify(crypto.pubkey(crypto.generate_key()), {"cap": 5000}, sig)


def test_verify_never_raises_on_garbage():
    assert not crypto.verify("ed25519:abc", {}, "ed25519:!!!")
    assert not crypto.verify("rsa:xyz", {}, "nope")


def test_mandate_signature(world):
    m = world.wallet.mandate
    trusted = {world.bank.kid: world.bank.issuer_pub}
    assert verify_signature(m, trusted)[0]
    forged = copy.deepcopy(m)
    forged["scope"]["per_txn_cap_inr"] = 10**6
    assert not verify_signature(forged, trusted)[0]
    assert not verify_signature({**m, "kid": "someone-else"}, trusted)[0]
