"""Terminal demo: `python -m kya.demo [attack-id|all|list]`."""
from __future__ import annotations

import os
import sys

from .agent.attacks import ATTACKS, POISONED_PAGE
from .world import World

if os.name == "nt":
    os.system("")  # enable ANSI escapes on Windows terminals
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

C = {"pass": "\033[32m", "fail": "\033[31m", "warn": "\033[33m", "pending": "\033[33m", "skip": "\033[90m",
     "b": "\033[1m", "dim": "\033[2m", "x": "\033[0m", "cyan": "\033[36m"}
ICON = {"pass": "✓", "fail": "✗", "warn": "!", "pending": "…", "skip": "·"}
BADGE = {"ALLOW": "\033[42;30m ALLOW \033[0m", "BLOCK": "\033[41;37m BLOCK \033[0m",
         "STEP_UP": "\033[43;30m STEP-UP \033[0m"}


def show(step: dict) -> None:
    d = step["decision"]
    print(f"\n  {C['b']}{step['label']}{C['x']}")
    for c in d["checks"]:
        col = C[c["status"]]
        print(f"    {col}{ICON[c['status']]} {c['layer']} {c['name']:<22}{C['x']} {C['dim']}{c['detail']}{C['x']}")
    where = f" at {d['layer']}" if d["layer"] else ""
    print(f"    → {BADGE[d['outcome']]}{where}  {d['reason']}")
    if "result" in step:
        print(f"    {C['dim']}bank: {step['result']}{C['x']}")


def run(attack_id: str) -> None:
    w = World()
    a = ATTACKS[attack_id]
    print(f"\n{C['cyan']}{C['b']}━━ {a.title} {C['x']}{C['dim']}({a.threat}, expected: {a.expected_layer}){C['x']}")
    if attack_id == "injection_amount":
        print(f"{C['dim']}  The agent was asked to buy groceries and read this product page:{C['x']}")
        for line in POISONED_PAGE.splitlines():
            print(f"{C['dim']}  │ {line}{C['x']}")
        print(f"  {C['dim']}The (hijacked) agent obeyed. What the bank sees:{C['x']}")
    for step in a.run(w):
        show(step)


def main(argv: list[str]) -> None:
    arg = argv[0] if argv else "injection_amount"
    if arg == "list":
        for a in ATTACKS.values():
            print(f"{a.id:<20} {a.expected_layer}  {a.title}")
        return
    if arg == "benign":
        w = World()
        print(f"\n{C['cyan']}{C['b']}━━ Normal task: buy groceries for ₹1,149{C['x']}")
        show({"label": "pay_merchant(m_freshmart, ₹1,149)", **w.call("pay_merchant", merchant_id="m_freshmart",
                                                                     amount_inr=1149)})
        return
    for attack_id in (ATTACKS if arg == "all" else [arg]):
        run(attack_id)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
