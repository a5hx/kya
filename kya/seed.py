"""Synthetic world: two fictional users, a merchant registry and 30 days of history.

Every identity here is invented. No real names, IDs, documents or faces.
"""
from __future__ import annotations

from .bank import Bank
from .face import template as ft
from .mandate import DAY

USERS = [
    {"user_id": "u_demo_001", "name": "Aarav Demo", "account": "ACC-0001", "kyc_ref": "kyc_synth_001",
     "id_number": "KYA-DEMO-4821-0937", "dob": "1998-04-12", "balance": 85_000, "face_seed": 1001},
    {"user_id": "u_demo_002", "name": "Meera Sample", "account": "ACC-0002", "kyc_ref": "kyc_synth_002",
     "id_number": "KYA-DEMO-7310-5528", "dob": "1994-11-03", "balance": 40_000, "face_seed": 1002},
]

MERCHANTS = [
    ("m_freshmart", "FreshMart Grocery", "5411", "Grocery", "freshmart@demobank"),
    ("m_organic", "Organic Basket", "5411", "Grocery", "organicbasket@demobank"),
    ("m_tiffin", "Tiffin Express", "5814", "Food delivery", "tiffinexpress@demobank"),
    ("m_quickcab", "QuickCab", "4121", "Cabs", "quickcab@demobank"),
    ("m_coinkart", "CoinKart Exchange", "6051", "Crypto / quasi-cash", "coinkart@demobank"),
    ("m_luxe", "Luxe Electronics", "5732", "Electronics", "luxe@demobank"),
]

# Aarav's last 30 days (all settled, made by Aarav himself, not by an agent).
HISTORY = {
    "m_freshmart": [820, 1150, 640, 980, 1320, 760, 905, 1040, 700, 1210],
    "m_tiffin": [320, 450, 280, 520, 390, 610, 350, 470, 300, 430],
    "m_quickcab": [180, 240, 310, 150, 420, 260, 200, 330, 190, 280],
}

HOUR_OFFSET = {"m_freshmart": 2, "m_tiffin": 5, "m_quickcab": 8}


def seed(bank: Bank) -> None:
    store, now = bank.store, bank.now()
    if store.user("u_demo_001"):
        return
    for u in USERS:
        tmpl = ft.synthetic(u["face_seed"])
        store.exec(
            "INSERT INTO users(user_id, name, account, kyc_ref, id_number, dob, face_template, face_hash, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (u["user_id"], u["name"], u["account"], u["kyc_ref"], u["id_number"], u["dob"], ft.to_blob(tmpl),
             ft.template_hash(tmpl), now - 60 * DAY))
        store.exec("INSERT INTO accounts(account, user_id, balance_inr) VALUES(?,?,?)",
                   (u["account"], u["user_id"], u["balance"]))
    for m in MERCHANTS:
        store.exec("INSERT INTO merchants(merchant_id, name, mcc, category, vpa) VALUES(?,?,?,?,?)", m)
    mcc = {m[0]: m[2] for m in MERCHANTS}
    for merchant_id, amounts in HISTORY.items():
        for i, amount in enumerate(amounts):
            ts = now - (29 - 3 * i) * DAY - HOUR_OFFSET[merchant_id] * 3600
            store.exec(
                "INSERT INTO txns(txn_id, account, mandate_id, merchant_id, mcc, amount_inr, ts, source, status) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (f"txn_hist_{merchant_id}_{i}", "ACC-0001", None, merchant_id, mcc[merchant_id], amount, ts,
                 "history", "settled"))
    bank._log("kyc.synthetic_seed", {"users": [u["user_id"] for u in USERS], "note": "synthetic identities only"})
