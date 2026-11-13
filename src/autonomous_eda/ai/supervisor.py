"""Durable, read-only engineering diagnosis orchestration."""

import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import cast
from uuid import uuid4

from ..diagnosis import Check
from ..knowledge.index import canonical, identity
from ..knowledge.retrieval import where
from ..knowledge.sources import check_sensitive
from ..mcp.registry import Registry
from .context import Knowledge, build, classify
from .contracts import VERSION, Context, render, selection
from .diagnosis import build_diagnosis
from .gateway import HttpProvider, InferenceError, Mock, Provider, Route, request_for
from .journal import JOURNAL_VERSION, TERMINAL, Journal, State, Workflow


@dataclass(frozen=True)
class Limits:
    deadline_seconds: int = 120
    request_seconds: int = 20
    max_attempts: int = 2
    token_budget: int = 32_768
    output_tokens: int = 256
    cost_budget_microdollars: int = 0

    def __post_init__(self) -> None:
        for key, value, lower, upper in (
            ("deadline_seconds", self.deadline_seconds, 1, 1800),
            ("request_seconds", self.request_seconds, 1, 120),
            ("max_attempts", self.max_attempts, 1, 4),
            ("token_budget", self.token_budget, 1, 262_144),
            ("output_tokens", self.output_tokens, 16, 4096),
            ("cost_budget_microdollars", self.cost_budget_microdollars, 0, 1_000_000),
        ):
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError(f"Invalid workflow limit: {key}")


@dataclass(frozen=True)
class Settings:
    registry: Registry
    knowledge: Knowledge | None = None
    default_route: Route = Route()
    routes: dict[str, Route] = field(default_factory=dict)
    limits: Limits = Limits()
    hosted_enabled: bool = False

    def route(self, task: str) -> Route:
        return (
            Route()
            if task in {"observe", "unrelated"}
            else self.routes.get(task, self.default_route)
        )


class Supervisor:
    def __init__(
        self,
        journal: Journal,
        settings: Settings,
        provider_factory: Callable[[Context, Route, bool], Provider] | None = None,
    ) -> None:
        if any(
            journal.path.resolve().is_relative_to(source.root)
            for source in settings.registry.experiments.values()
        ):
            raise ValueError("Keep the AI journal outside approved run directories")
        self.journal = journal
        self.settings = settings
        self.provider_factory = provider_factory

    def provider(self, context: Context, route: Route, consent: bool) -> Provider:
        if self.provider_factory:
            return self.provider_factory(context, route, consent)
        if route.adapter == "mock":
            return Mock(context)
        return HttpProvider(route, consent, self.settings.hosted_enabled)

    def policy_digest(self, experiment: str, route: Route) -> str:
        source = self.settings.registry.experiments.get(experiment)
        if source is None:
            raise ValueError("experiment_not_authorized")
        source.check()
        knowledge = self.settings.knowledge
        knowledge_policy = (
            (
                str(knowledge.path),
                knowledge.context_chars,
                {key: entry.signature for key, entry in knowledge.registry.items()},
            )
            if knowledge
            else None
        )
        return identity(
            str(source.root),
            source.identity,
            asdict(route),
            self.settings.hosted_enabled,
            knowledge_policy,
            asdict(self.settings.limits),
        )

    def start(
        self,
        experiment: str,
        question: str = "",
        *,
        observe: bool = False,
        filters: dict[str, str] | None = None,
        consent: bool = False,
        request_id: str | None = None,
        diagnosis_check: Check | None = None,
    ) -> dict[str, object]:
        if request_id is not None and not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", request_id):
            raise ValueError("Invalid request ID")
        question = question.strip()
        if not observe and (not question or len(question) > 1024 or not question.isprintable()):
            raise ValueError("Use a printable engineering question of 1–1024 characters")
        check_sensitive(question)
        if request_id is not None:
            check_sensitive(request_id)
        if observe:
            question = ""
        filters = filters or {}
        where(filters)
        check_sensitive(canonical(filters))
        if diagnosis_check is not None and (observe or filters):
            raise ValueError("Structured diagnosis takes a timing check and authorized experiment")
        task = "diagnose" if diagnosis_check else "observe" if observe else classify(question)
        route = self.settings.route(task)
        key = self.journal.create(
            {
                "experiment": experiment,
                "question": question,
                "observe": observe,
                "filters": filters,
                "consent": consent,
                "task": task,
                "policy": self.policy_digest(experiment, route),
                "route": asdict(route),
                "limits": asdict(self.settings.limits),
                "schema_version": VERSION,
                **({"diagnosis_check": diagnosis_check} if diagnosis_check else {}),
            },
            self.settings.limits.deadline_seconds,
            request_id,
        )
        return self.resume(key)

    def details(self, key: str, *, historical: bool = True) -> dict[str, object]:
        workflow, history = self.journal.inspect(key)
        experiment = str(workflow.inputs["experiment"])
        if experiment not in self.settings.registry.experiments:
            raise ValueError("experiment_not_authorized")
        route = self.settings.route(str(workflow.inputs["task"]))
        if workflow.inputs["policy"] != self.policy_digest(experiment, route):
            raise ValueError("workflow_policy_changed")
        output = workflow.data.get("output")
        if output is not None and identity(
            output, workflow.data.get("selection")
        ) != workflow.data.get("output_digest"):
            raise ValueError("workflow_output_integrity_failed")
        stale = False
        if not historical and output is not None:
            try:
                fresh = self.context(workflow)
                stale = fresh.digest() != workflow.data.get("context")
            except (OSError, ValueError, sqlite3.Error, KeyError, TypeError):
                stale = True
            if stale:
                output = None
        return {
            "workflow_id": key,
            "schema_version": JOURNAL_VERSION,
            "input_digest": workflow.input_digest,
            "state": workflow.state,
            "deadline": workflow.deadline,
            "transitions": history,
            "task": workflow.inputs["task"],
            "route": workflow.inputs["route"],
            "experiment_id": experiment,
            "budgets": workflow.inputs["limits"],
            "snapshot": workflow.data.get("snapshot"),
            "context_digest": workflow.data.get("context"),
            "model_attempts": workflow.data.get("attempts", []),
            "output": output,
            "recorded_selection": workflow.data.get("selection") if historical else None,
            "reason": "stale_evidence" if stale else workflow.data.get("reason"),
            "historical": historical,
        }

    def context(self, workflow: Workflow) -> Context:
        question = str(workflow.inputs["question"])
        if workflow.inputs["task"] == "diagnose":
            return build_diagnosis(
                self.settings.registry,
                str(workflow.inputs["experiment"]),
                question,
                cast(Check, workflow.inputs["diagnosis_check"]),
                self.settings.knowledge,
            )
        task = classify(question) if not workflow.inputs["observe"] else "observe"
        return build(
            self.settings.registry,
            str(workflow.inputs["experiment"]),
            question,
            task,
            self.settings.knowledge,
            cast(dict[str, str], workflow.inputs["filters"]),
        )

    def resume(self, key: str, *, stop_after: State | None = None) -> dict[str, object]:
        owner = uuid4().hex
        limits = self.settings.limits
        lease = max(45, limits.request_seconds + 15)
        completed_here = False
        try:
            while True:
                workflow = self.journal.claim(key, owner, lease)
                if workflow.state in TERMINAL:
                    break
                route = self.settings.route(str(workflow.inputs["task"]))
                if workflow.inputs["policy"] != self.policy_digest(
                    str(workflow.inputs["experiment"]), route
                ):
                    raise ValueError("workflow_policy_changed")
                if time.time() >= workflow.deadline:
                    self.finish(workflow, owner, State.FAILED, "deadline_exceeded")
                else:
                    self.step(workflow, owner, route)
                current, _ = self.journal.inspect(key)
                if current.state in TERMINAL or current.state == stop_after:
                    completed_here = current.state in TERMINAL
                    break
        except ValueError as error:
            if str(error) != "workflow_lease_lost":
                raise
            current, _ = self.journal.inspect(key)
            if current.state != State.CANCELLED:
                raise
        finally:
            self.journal.release(key, owner)
        # Newly produced output has already been validated in this execution.
        return self.details(key, historical=False) if not completed_here else self.result(key)

    def result(self, key: str) -> dict[str, object]:
        value = self.details(key)
        value["historical"] = False
        value["recorded_selection"] = None
        return value

    def finish(
        self,
        workflow: Workflow,
        owner: str,
        state: State,
        reason: str,
        context: Context | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        if state != State.FAILED and time.time() >= workflow.deadline:
            state, reason, context = State.FAILED, "deadline_exceeded", None
        changes: dict[str, object] = {"reason": reason}
        if context is not None:
            chosen = workflow.data.get("selection")
            selected = (
                selection(canonical(chosen), context)
                if state == State.COMPLETED and chosen
                else None
            )
            output = render(context, selected, metadata or {})
            changes.update(
                output=output,
                output_digest=identity(output, chosen),
                context=context.digest(),
                snapshot=context.snapshot,
            )
        self.journal.update(workflow, owner, state, reason, changes)

    def step(self, workflow: Workflow, owner: str, route: Route) -> None:
        state = workflow.state
        if state == State.CREATED:
            self.journal.update(workflow, owner, State.VALIDATING, "validated_input")
            return
        if state == State.VALIDATING:
            self.journal.update(workflow, owner, State.COLLECTING_EVIDENCE, "scope_authorized")
            return
        if state == State.COLLECTING_EVIDENCE:
            self.journal.update(workflow, owner, State.RETRIEVING_CONTEXT, "collecting_snapshot")
            return
        if state == State.SELECTING_MODEL:
            self.journal.update(workflow, owner, State.GENERATING_EXPLANATION, "route_selected")
            return
        try:
            context = self.context(workflow)
        except (
            OSError,
            ValueError,
            sqlite3.Error,
            KeyError,
            TypeError,
            RecursionError,
            OverflowError,
        ):
            self.finish(workflow, owner, State.FAILED, "evidence_unavailable")
            return
        if state == State.RETRIEVING_CONTEXT:
            self.journal.update(
                workflow,
                owner,
                State.CHECKING_SUFFICIENCY,
                "context_built",
                {
                    "context": context.digest(),
                    "snapshot": context.snapshot,
                    "retrieval_events": context.retrieval_events,
                },
            )
            return
        if context.digest() != workflow.data.get("context"):
            self.finish(workflow, owner, State.FAILED, "stale_evidence")
            return
        if state == State.CHECKING_SUFFICIENCY:
            if not context.sufficient:
                self.finish(
                    workflow, owner, State.INSUFFICIENT_EVIDENCE, "insufficient_evidence", context
                )
            elif context.task == "observe":
                self.finish(workflow, owner, State.COMPLETED, "observed", context)
            elif context.task == "diagnose" and route.adapter == "disabled":
                self.finish(workflow, owner, State.COMPLETED, "deterministic_diagnosis", context)
            elif route.adapter == "disabled":
                self.finish(workflow, owner, State.FAILED, "inference_disabled", context)
            elif route.adapter == "openai" and not (
                self.settings.hosted_enabled and workflow.inputs["consent"] is True
            ):
                self.finish(workflow, owner, State.FAILED, "hosted_consent_required", context)
            else:
                self.journal.update(workflow, owner, State.SELECTING_MODEL, "evidence_sufficient")
        elif state == State.GENERATING_EXPLANATION:
            self.generate(workflow, owner, route, context)
        elif state == State.VALIDATING_OUTPUT:
            attempts = cast(list[dict[str, object]], workflow.data["attempts"])
            self.finish(
                workflow,
                owner,
                State.COMPLETED,
                "validated_explanation",
                context,
                {
                    "adapter": route.adapter,
                    "model": route.model_id,
                    "fixture": route.adapter == "mock",
                    "output_schema_version": VERSION,
                    "attempt": attempts[-1],
                },
            )
        else:
            raise ValueError("invalid_workflow_state")

    def generate(self, workflow: Workflow, owner: str, route: Route, context: Context) -> None:
        limits = self.settings.limits
        attempts = cast(list[dict[str, object]], workflow.data.get("attempts", []))
        if attempts and attempts[-1]["status"] == "reserved" and route.adapter == "openai":
            self.finish(workflow, owner, State.FAILED, "hosted_request_outcome_unknown", context)
            return
        if attempts and attempts[-1].get("retryable") is False:
            self.finish(workflow, owner, State.FAILED, str(attempts[-1]["status"]), context)
            return
        request = request_for(route, context, limits.output_tokens, repair=bool(attempts))
        reserved = request.input_reserve + request.output_tokens
        cost = route.cost(request.input_reserve, request.output_tokens)
        if len(attempts) >= limits.max_attempts:
            self.finish(workflow, owner, State.FAILED, "attempt_budget_exhausted", context)
            return
        if sum(cast(int, a["tokens_charged"]) for a in attempts) + reserved > limits.token_budget:
            self.finish(workflow, owner, State.FAILED, "token_budget_exhausted", context)
            return
        if (
            sum(cast(int, a["cost_charged"]) for a in attempts) + cost
            > limits.cost_budget_microdollars
        ):
            self.finish(workflow, owner, State.FAILED, "cost_budget_exhausted", context)
            return
        timeout = min(limits.request_seconds, workflow.deadline - time.time())
        if timeout <= 0:
            self.finish(workflow, owner, State.FAILED, "deadline_exceeded")
            return
        attempt: dict[str, object] = {
            "status": "reserved",
            "context_digest": request.context_digest,
            "schema_version": VERSION,
            "classification": request.data_class,
            "tokens_charged": reserved,
            "cost_charged": cost,
            "number": len(attempts) + 1,
        }
        self.journal.update(
            workflow, owner, workflow.state, "attempt_reserved", {"attempts": [*attempts, attempt]}
        )
        workflow, _ = self.journal.inspect(workflow.id)
        if workflow.state == State.CANCELLED:
            return
        started = time.monotonic()
        provider = self.provider(context, route, workflow.inputs["consent"] is True)
        error: str | None = None
        retryable = False
        chosen = None
        try:
            response = provider.complete(request, timeout)
            attempt.update(input_tokens=response.input_tokens, output_tokens=response.output_tokens)
            if response.reported_model:
                check_sensitive(response.reported_model)
                attempt["reported_model"] = response.reported_model
            if response.input_tokens is not None and response.output_tokens is not None:
                attempt["tokens_charged"] = response.input_tokens + response.output_tokens
                attempt["cost_charged"] = route.cost(response.input_tokens, response.output_tokens)
            if (response.input_tokens or 0) > request.input_reserve or (
                response.output_tokens or 0
            ) > request.output_tokens:
                raise InferenceError("provider_budget_violation")
            chosen = selection(response.text, context)
        except InferenceError as exc:
            error, retryable = str(exc), exc.retryable
        except ValueError:
            error, retryable = "invalid_model_selection", True
        attempt.update(
            status=error or "validated_selection",
            retryable=retryable,
            duration_ms=(time.monotonic() - started) * 1000,
        )
        changes: dict[str, object] = {"attempts": [*attempts, attempt]}
        if error is None and chosen is not None:
            changes["selection"] = asdict(chosen)
            self.journal.update(
                workflow, owner, State.VALIDATING_OUTPUT, "selection_recorded", changes
            )
        else:
            self.journal.update(workflow, owner, workflow.state, "attempt_failed", changes)
            if not retryable:
                current, _ = self.journal.inspect(workflow.id)
                if context.task == "diagnose":
                    # This provider failure can still return deterministic findings.
                    # Verify them again before attaching output to the current result.
                    try:
                        fresh = self.context(current)
                    except (OSError, ValueError, sqlite3.Error, KeyError, TypeError):
                        self.finish(current, owner, State.FAILED, "stale_evidence")
                        return
                    if fresh.digest() != context.digest():
                        self.finish(current, owner, State.FAILED, "stale_evidence")
                        return
                self.finish(
                    current, owner, State.FAILED, error or "invalid_model_selection", context
                )
