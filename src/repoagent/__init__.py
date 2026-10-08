"""SHI Agent core package."""

from .agent.loop import AgentLoop
from .contracts import TaskSpec

__all__ = ["AgentLoop", "TaskSpec"]
__version__ = "0.1.0"
