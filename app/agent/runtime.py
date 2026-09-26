import inspect
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.agent.routing import (
    deterministic_action,
    is_ambiguous_followup,
    sanitize_text,
    validated_model_advice,
)
from app.agent.errors import ResumeConflictError, ThreadNotFoundError  # noqa: F401 (re-exported)
from app.agent.state import CareerGraphState
from app.agent.tools import CareerGraphTools
from app.llm.factory import build_chat_model
from app.models.chat import ChatResponse, ChatResumeRequest, ChatRunRequest
from app.services.career_agent import CareerAgent
from app.storage.graph_checkpoint import TurnReceiptStore




async def _maybe_await(value):
    return await value if inspect.isawaitable(value) else value


class CareerGraphRuntime:
    """One bounded LangGraph runtime; all side effects delegate to existing services."""

    def __init__(self, *, checkpoint_path: str | Path, career_agent=None, tools=None, model=None):
        self.checkpoint_path = Path(checkpoint_path)
        self.career_agent = career_agent or CareerAgent()
        self.tools = tools or CareerGraphTools()
        self.model = model or build_chat_model()
        self.receipts = TurnReceiptStore(self.checkpoint_path)

    def _builder(self, transient: dict | None = None):
        transient = transient or {}
        builder = StateGraph(CareerGraphState)

        async def load_context(state):
            current = sanitize_text(state.get("latest_user_message"), 1000)
            previous = sanitize_text(state.get("conversation_summary"), 700)
            summary = sanitize_text(
                (previous + " | " if previous else "") + "User request: " + current,
                1000,
            )
            has_reference = bool(
                state.get("selected_job_id") or state.get("application_id") or state.get("draft_id")
            )
            action = (
                "needs_clarification"
                if is_ambiguous_followup(current, has_reference=has_reference)
                else deterministic_action(current)
            )
            if action == "send_draft" and not state.get("draft_id"):
                action = "needs_clarification"
            return {
                "latest_user_message": current,
                "conversation_summary": summary,
                "action": action,
                "stage": "completed",
                "response_text": "",
                "advisory_roles": [],
                "model_explanation": None,
            }

        async def model_assist(state):
            advice = await validated_model_advice(self.model, {
                "message": state.get("latest_user_message", ""),
                "conversation_summary": state.get("conversation_summary", ""),
                "deterministic_action": state.get("action"),
                "career_session_id": state.get("career_session_id"),
            })
            # Model output is advisory. It cannot replace deterministic routing.
            roles = advice.decision.advisory_roles if advice.decision else []
            action = state.get("action")
            # A validated structured result may disambiguate read-only work only.
            # Tracker/outreach/send actions still require deterministic explicit intent.
            if action == "career_advice" and advice.decision:
                if advice.decision.needs_clarification:
                    action = "needs_clarification"
                elif advice.decision.action in ("search_jobs", "career_advice"):
                    action = advice.decision.action
            return {
                "action": action,
                "advisory_roles": roles,
                "model_explanation": advice.explanation,
            }

        async def search_jobs(state):
            result = await self.career_agent.search(
                state["latest_user_message"],
                include_seen=bool(state.get("include_seen")),
                session_id=state.get("career_session_id"),
                strict_mode=state.get("strict_mode"),
                refresh=bool(state.get("refresh")),
            )
            ids = [item["id"] for item in result.get("results", []) if item.get("id")][:50]
            return {
                "stage": "completed",
                "response_text": sanitize_text(result.get("summary") or "Search completed.", 4000),
                "career_session_id": result.get("session_id"),
                "recommendation_ids": ids,
            }

        async def career_advice(state):
            explanation = state.get("model_explanation")
            return {
                "stage": "completed",
                "response_text": explanation or (
                    "Tell me the roles, locations, or career question you want help with."
                ),
            }

        async def clarify(_state):
            return {
                "stage": "needs_clarification",
                "response_text": "Please identify the job, application, or draft you mean.",
            }

        async def unsupported_action(_state):
            return {
                "stage": "needs_clarification",
                "response_text": "Please provide the stored job or application you want to use.",
            }

        async def save_application(state):
            try:
                application = await _maybe_await(self.tools.save_application({**state, **transient}))
            except Exception:
                return await unsupported_action(state)
            return {
                "stage": "completed",
                "response_text": "The selected job is saved in the tracker.",
                "application_id": application["id"],
            }

        async def update_application(state):
            try:
                application = await _maybe_await(self.tools.update_application({**state, **transient}))
            except Exception:
                return await unsupported_action(state)
            return {
                "stage": "completed",
                "response_text": "The tracker application was updated.",
                "application_id": application["id"],
            }

        async def prepare_outreach(state):
            try:
                draft = await _maybe_await(self.tools.prepare_outreach({**state, **transient}))
            except Exception:
                return {
                    "stage": "needs_clarification",
                    "response_text": "The outreach draft could not be prepared from those stored references.",
                    "draft_id": None,
                }
            return {
                "stage": "awaiting_send_confirmation",
                "response_text": "Review the editable draft, then confirm once to approve and send it.",
                "draft_id": draft["id"],
                "application_id": draft.get("application_id") or state.get("application_id"),
            }

        async def confirm_send(state):
            decision = interrupt({
                "kind": "final_send_confirmation",
                "draft_id": state.get("draft_id"),
                "message": "Confirm the final reviewed draft to approve and send once.",
            })
            confirmed = bool(decision.get("confirmed")) if isinstance(decision, dict) else bool(decision)
            return {"confirmed": confirmed}

        async def await_send_confirmation(_state):
            return {
                "stage": "awaiting_send_confirmation",
                "response_text": "Review the editable draft, then confirm once to approve and send it.",
            }

        async def send_draft(state):
            try:
                sent = await _maybe_await(self.tools.approve_and_send(state["draft_id"]))
            except Exception:
                return {
                    "stage": "completed",
                    "response_text": "The draft could not be sent. Review its current status before any new action.",
                }
            return {
                "stage": "completed",
                "response_text": "The approved draft was sent.",
                "draft_id": sent.get("id", state.get("draft_id")),
            }

        async def cancel_send(_state):
            return {
                "stage": "cancelled",
                "response_text": "Send cancelled. The draft was not approved or sent.",
            }

        builder.add_node("load_context", load_context)
        builder.add_node("model_assist", model_assist)
        builder.add_node("search_jobs", search_jobs)
        builder.add_node("career_advice", career_advice)
        builder.add_node("clarify", clarify)
        builder.add_node("unsupported_action", unsupported_action)
        builder.add_node("save_application", save_application)
        builder.add_node("update_application", update_application)
        builder.add_node("prepare_outreach", prepare_outreach)
        builder.add_node("confirm_send", confirm_send)
        builder.add_node("await_send_confirmation", await_send_confirmation)
        builder.add_node("send_draft", send_draft)
        builder.add_node("cancel_send", cancel_send)
        builder.add_edge(START, "load_context")
        builder.add_edge("load_context", "model_assist")
        builder.add_conditional_edges(
            "model_assist",
            lambda state: state.get("action", "career_advice"),
            {
                "search_jobs": "search_jobs",
                "career_advice": "career_advice",
                "needs_clarification": "clarify",
                "prepare_outreach": "prepare_outreach",
                "send_draft": "await_send_confirmation",
                "select_job": "unsupported_action",
                "save_application": "save_application",
                "update_application": "update_application",
                "none": "career_advice",
            },
        )
        builder.add_conditional_edges(
            "prepare_outreach",
            lambda state: "confirm" if state.get("draft_id") else "stop",
            {"confirm": "confirm_send", "stop": END},
        )
        builder.add_edge("await_send_confirmation", "confirm_send")
        builder.add_conditional_edges(
            "confirm_send",
            lambda state: "send" if state.get("confirmed") else "cancel",
            {"send": "send_draft", "cancel": "cancel_send"},
        )
        for node in (
            "search_jobs", "career_advice", "clarify", "unsupported_action",
            "save_application", "update_application", "send_draft", "cancel_send",
        ):
            builder.add_edge(node, END)
        return builder

    @staticmethod
    def _config(thread_id: str):
        return {"configurable": {"thread_id": thread_id}}

    @staticmethod
    def _response(state: dict, thread_id: str, turn_id: str) -> ChatResponse:
        return ChatResponse(
            thread_id=thread_id,
            turn_id=turn_id,
            stage=state.get("stage", "completed"),
            message=sanitize_text(state.get("response_text"), 4000),
            career_session_id=state.get("career_session_id"),
            recommendation_ids=state.get("recommendation_ids") or [],
            advisory_roles=state.get("advisory_roles") or [],
            draft_id=state.get("draft_id"),
            application_id=state.get("application_id"),
        )

    async def run(self, request: ChatRunRequest) -> ChatResponse:
        payload = request.model_dump_json(exclude_none=True)
        cached = self.receipts.claim(request.thread_id, request.turn_id, payload)
        if cached is not None:
            return ChatResponse.model_validate(cached)
        try:
            initial = {
                "thread_id": request.thread_id,
                "turn_id": request.turn_id,
                # LangGraph checkpoints the input before the first node executes.
                # Sanitize before invocation so historical checkpoints are safe too.
                "latest_user_message": sanitize_text(request.message, 1000),
                "career_session_id": request.career_session_id,
                "selected_job_id": request.selected_job_id,
                "application_id": request.application_id,
                "contact_id": request.contact_id,
                "draft_id": request.draft_id,
                "include_seen": request.include_seen,
                "strict_mode": request.strict_mode,
                "refresh": request.refresh,
            }
            self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            async with AsyncSqliteSaver.from_conn_string(str(self.checkpoint_path)) as saver:
                await saver.setup()
                graph = self._builder({
                    "recipient": request.recipient,
                    "application_status": request.application_status,
                    "notes": request.notes,
                }).compile(checkpointer=saver)
                state = await graph.ainvoke(initial, self._config(request.thread_id))
            response = self._response(state, request.thread_id, request.turn_id)
            self.receipts.complete(request.thread_id, request.turn_id, response.model_dump(mode="json"))
            return response
        except Exception:
            self.receipts.abandon(request.thread_id, request.turn_id)
            raise

    async def resume(self, request: ChatResumeRequest) -> ChatResponse:
        payload = request.model_dump_json(exclude_none=True)
        cached = self.receipts.claim(request.thread_id, request.turn_id, payload)
        if cached is not None:
            return ChatResponse.model_validate(cached)
        try:
            self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            async with AsyncSqliteSaver.from_conn_string(str(self.checkpoint_path)) as saver:
                await saver.setup()
                graph = self._builder().compile(checkpointer=saver)
                snapshot = await graph.aget_state(self._config(request.thread_id))
                if not snapshot.values or not snapshot.next:
                    raise ThreadNotFoundError("No interrupted conversation is available to resume.")
                if snapshot.values.get("draft_id") != request.draft_id:
                    raise ResumeConflictError("The draft does not match the pending confirmation.")
                await _maybe_await(self.tools.update_draft(
                    request.draft_id,
                    subject=request.edited_subject,
                    body=request.edited_body,
                ))
                state = await graph.ainvoke(
                    Command(resume={"confirmed": request.confirmed}),
                    self._config(request.thread_id),
                )
            response = self._response(state, request.thread_id, request.turn_id)
            self.receipts.complete(request.thread_id, request.turn_id, response.model_dump(mode="json"))
            return response
        except Exception:
            self.receipts.abandon(request.thread_id, request.turn_id)
            raise

    async def safe_state(self, thread_id: str) -> dict:
        if not self.checkpoint_path.exists():
            return {}
        async with AsyncSqliteSaver.from_conn_string(str(self.checkpoint_path)) as saver:
            graph = self._builder().compile(checkpointer=saver)
            snapshot = await graph.aget_state(self._config(thread_id))
        allowed = {
            "latest_user_message", "conversation_summary", "thread_id", "turn_id", "action",
            "stage", "response_text", "career_session_id", "recommendation_ids",
            "selected_job_id", "application_id", "contact_id", "draft_id", "advisory_roles",
        }
        return {key: value for key, value in snapshot.values.items() if key in allowed}
