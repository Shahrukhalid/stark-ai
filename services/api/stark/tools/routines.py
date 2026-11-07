"""Routines by voice: "Wanda, every Monday at 9 draft three posts" becomes an automation (asks first)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..core.tool_registry import ApprovalText, Risk, Tool, ToolResult

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


class RoutineInput(BaseModel):
    name: str = Field(min_length=1, max_length=80, description="short name, e.g. 'Monday post ideas'")
    every: Literal["day", "week"]
    weekday: Literal["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"] | None = None
    time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="local time, 24-hour, e.g. 09:00")
    task: str = Field(min_length=3, max_length=2000, description="what the assistant should do each time")
    agent: Literal["vision", "wanda"] | None = Field(None, description="who does it (default: you)")


class CreateRoutine(Tool):
    id = "routine.create"
    category = "automation"
    description = ("Create a repeating routine (every day or every week at a time) in which you or the other "
                   "assistant do a task, e.g. draft posts or check analytics. It becomes an automation the user "
                   "can see and switch off; it never publishes. Asks first.")
    Input = RoutineInput
    risk = Risk.WRITE

    def _when(self, a: RoutineInput) -> str:
        return f"every {a.weekday.title()} at {a.time}" if a.every == "week" and a.weekday else f"every day at {a.time}"

    def describe(self, args: RoutineInput, ctx) -> ApprovalText:
        who = (args.agent or (ctx.agent.id if ctx and ctx.agent else "vision")).upper()
        return ApprovalText(f"Create routine '{args.name}' ({self._when(args)})", args.name,
                            f"{who} will: {args.task[:300]}", "Runs on its own until you switch it off in Automations.")

    async def run(self, args: RoutineInput, ctx) -> ToolResult:
        if args.every == "week" and not args.weekday:
            return ToolResult(False, "say which day of the week")
        agent = args.agent or (ctx.agent.id if ctx.agent else "vision")
        engine = getattr(ctx.kernel, "automations", None)
        if engine is None:
            return ToolResult(False, "automations are not running")
        trig = {"type": "schedule", "every": args.every, "time": args.time}
        if args.every == "week":
            trig["weekday"] = DAYS.index(args.weekday)
        try:
            a = engine.save({"name": args.name, "enabled": True, "trigger": trig, "conditions": [],
                             "actions": [{"type": "ask_agent", "agent": agent, "message": args.task}]})
        except Exception as exc:  # noqa: BLE001 - AutomationError has a user-facing message
            return ToolResult(False, str(exc)[:200])
        return ToolResult(True, f"Routine '{args.name}' created: {self._when(args)}", {"id": a["id"]})


class NoInput(BaseModel):
    pass


class ListRoutines(Tool):
    id = "routine.list"
    category = "automation"
    description = "List the user's scheduled routines and automations with their times and whether they are on."
    Input = NoInput
    risk = Risk.READ

    async def run(self, args, ctx) -> ToolResult:
        engine = getattr(ctx.kernel, "automations", None)
        items = engine.list() if engine else []
        return ToolResult(True, f"{len(items)} automations", {"automations": [
            {"name": a["name"], "on": a["enabled"], "trigger": a["trigger"],
             "does": [s.get("message") or s.get("type") for s in a.get("actions", [])]} for a in items]})


class SummaryInput(BaseModel):
    language: Literal["en", "ur"] = "en"


class DailySummary(Tool):
    id = "assistant.summary"
    category = "automation"
    description = ("Today's summary: posts scheduled today, posts published yesterday, failures, items waiting "
                   "for approval, accounts that need attention and today's reminders.")
    Input = SummaryInput
    risk = Risk.READ

    async def run(self, args: SummaryInput, ctx) -> ToolResult:
        from ..routines import summary_text

        return ToolResult(True, summary_text(ctx.kernel, urdu=args.language == "ur"))


TOOLS = [CreateRoutine(), ListRoutines(), DailySummary()]
