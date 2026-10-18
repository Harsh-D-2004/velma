from dataclasses import dataclass, field
from typing import Any
from collections.abc import Callable


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict                    # JSON schema for the arguments
    fn: Callable[[dict, Any], Any]      # fn(args, tool_context) -> JSON-serialisable result


@dataclass
class AgentSpec:
    system_prompt: str
    task_prompt: str                    # first user message
    registry: Any                       # services.agent.registry.Registry
    max_steps: int = 0                  # 0 = settings.max_steps
    nudge: str = "You must call a tool. When all work is done, call finish."
