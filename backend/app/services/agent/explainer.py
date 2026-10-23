from pathlib import Path

from app.models.agent import AgentSpec
from app.services.agent.registry import Registry
from app.services.agent.tools import check, content, draw, finish, sandbox, terms

SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system.md").read_text()
TASK_PROMPT = "Turn the article of this job into an explainer. Start with outline."


def build_registry() -> Registry:
    by_name = {t.name: t for m in (content, check, terms, draw, sandbox, finish) for t in m.TOOLS}
    order = ["outline", "rewrite", "check", "web_search", "explain_term", "draw",
             "bash", "finish"]  # spec section 10
    return Registry([by_name[n] for n in order])


def build_spec() -> AgentSpec:
    return AgentSpec(system_prompt=SYSTEM_PROMPT, task_prompt=TASK_PROMPT, registry=build_registry())
