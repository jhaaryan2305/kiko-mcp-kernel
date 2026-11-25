"""Runtime settings, read from environment variables only.

No secrets live here. AWS uses the default credential chain; ElevenLabs is
configured in the HUD. See ``.env.example`` for every supported variable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from .policy import default_policy_path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    policy_path: Path
    aws_region: Optional[str]
    relay_target_arn: Optional[str]
    relay_role_arn: Optional[str]

    @property
    def ledger_path(self) -> Path:
        return self.data_dir / "ledger.sqlite3"

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        env = os.environ if env is None else env
        data_dir = Path(env["KIKO_DATA_DIR"]).expanduser() if env.get("KIKO_DATA_DIR") else Path.home() / ".kiko"
        policy = Path(env["KIKO_POLICY_PATH"]).expanduser() if env.get("KIKO_POLICY_PATH") else default_policy_path()
        return cls(
            data_dir=data_dir,
            policy_path=policy,
            aws_region=env.get("AWS_REGION") or None,
            relay_target_arn=env.get("KIKO_RELAY_TARGET_ARN") or None,
            relay_role_arn=env.get("KIKO_RELAY_ROLE_ARN") or None,
        )
