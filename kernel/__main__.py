"""``python -m kernel``: load and validate the policy, open the ledger, print a summary.

TODO(sprint): start the FastMCP gateway and the HUD bridge instead of exiting.
"""
from __future__ import annotations

import sys
from collections import Counter

from .config import Settings
from .ledger import UndoLedger
from .policy import PolicyEngine, PolicyError


def main() -> int:
    settings = Settings.from_env()
    try:
        policy = PolicyEngine.from_file(settings.policy_path)
    except PolicyError as exc:
        print("policy rejected: {}".format(exc), file=sys.stderr)
        return 1

    by_tier = Counter(t.tier.value for t in policy.tools.values())
    print("K.I.K.O. kernel · policy {}".format(policy.version))
    for tier, count in sorted(by_tier.items()):
        print("  {:<24} {} tools".format(tier, count))

    with UndoLedger(settings.ledger_path) as ledger:
        pending = ledger.pending()
        print("  ledger: {} ({} pending)".format(settings.ledger_path, len(pending)))
    print("gateway: not started (TODO)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
