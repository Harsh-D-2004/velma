import logging

import openai
from app.core.settings import settings
from app.services.llm import text
from app.services.llm.budget import TokenBudget
from app.services.llm.config import token_budget

log = logging.getLogger("velma.agent")


def _run_call(spec, ctx, tctx, call: dict, on_step) -> None:
    result = spec.registry.execute(call["name"], call["arguments"], tctx)
    ctx.tool_calls += 1
    ctx.messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
    on_step(ctx.snapshot())


def run_agent(spec, ctx, tctx, on_step) -> str:
    if not ctx.messages:
        ctx.messages = [{"role": "system", "content": spec.system_prompt},
                        {"role": "user", "content": spec.task_prompt}]
    pending = ctx.pending_tool_calls()
    if pending:
        log.info("agent resume pending_tool_calls=%d steps=%d", len(pending), ctx.steps)
    for p in pending:  # resumed mid-step
        _run_call(spec, ctx, tctx, {"id": p["id"], "name": p["function"]["name"],
                                    "arguments": p["function"]["arguments"]}, on_step)
    max_steps = spec.max_steps or settings.max_steps
    limit = token_budget(len(ctx.article.split()))
    budget = TokenBudget(limit)
    tools = spec.registry.schemas()
    tool_choice = "required"  # makes plain-text replies rare; dropped for this job if the endpoint rejects it
    while True:
        if ctx.finished:
            return "finished"
        if ctx.steps >= max_steps or budget.over(ctx.tokens):
            log.info("agent stop reason=limit steps=%d tokens=%d", ctx.steps, ctx.tokens)
            return "limit"
        if budget.warn(ctx.tokens) and not ctx.warned:
            ctx.warned = True
            log.info("agent budget_warn tokens=%d limit=%d", ctx.tokens, limit)
            ctx.messages.append({"role": "user", "content": "Token budget is almost used up. Wrap up and call finish now."})
        try:
            res = text.chat(ctx.messages, task="agent", ctx=ctx, tools=tools, tool_choice=tool_choice)
        except text.ModelCallsFailed as e:
            if tool_choice == "required" and isinstance(e.__cause__, openai.BadRequestError):
                log.warning("agent tool_choice=required rejected, falling back to auto")
                tool_choice = "auto"
                continue
            raise
        ctx.steps += 1
        ctx.messages.append(res.message)
        log.info("agent step=%d tool_calls=%d tools=%s tokens_total=%d", ctx.steps, len(res.tool_calls),
                 ",".join(c["name"] for c in res.tool_calls), ctx.tokens)
        if not res.tool_calls:
            if ctx.nudges >= settings.max_nudges:
                log.info("agent give_up reason=no_finish nudges=%d", ctx.nudges)
                return "no_finish"
            ctx.nudges += 1
            log.info("agent nudge n=%d reply_chars=%d", ctx.nudges, len(res.content))
            ctx.messages.append({"role": "user", "content": spec.nudge})
            on_step(ctx.snapshot())
            continue
        for call in res.tool_calls:
            _run_call(spec, ctx, tctx, call, on_step)
