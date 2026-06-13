"""Boss interaction channels (HITL approval I/O).

The graph stays decoupled from *how* the Boss is reached: it interrupts and a
BossChannel adapter delivers the approval request and returns the verdict.
Phase 1 ships the CLI adapter; Phase 2 adds Feishu / Discord behind the same
port without touching the graph.
"""

from .base import BossChannel
from .cli_channel import CLIChannel


def get_channel(kind: str | None = None) -> BossChannel:
    """Factory: resolve a BossChannel from config (or explicit kind)."""
    from ..config import get_config

    kind = kind or get_config().app.channel.kind
    if kind == "cli":
        return CLIChannel()
    if kind == "feishu":
        from .feishu_channel import FeishuChannel

        return FeishuChannel()
    if kind == "discord":
        raise NotImplementedError("discord channel is a later deliverable")
    raise ValueError(f"Unknown channel kind: {kind!r}")


__all__ = ["BossChannel", "CLIChannel", "get_channel"]
