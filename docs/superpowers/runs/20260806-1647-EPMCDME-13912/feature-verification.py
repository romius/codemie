# ruff: noqa: T201
"""End-to-end feature verification for the A2UI migration.

Drives the real backend code path: tool emits a surface -> envelopes are persisted the way
serve_data persists them -> a client action envelope (shaped exactly as the frontend builds
it) comes back -> intake validates it against the stored surface -> both sides are
materialized into the text the LLM sees on the next turn.

No mocks except the stream generator, which is the transport.
"""

import json
from unittest.mock import MagicMock

from codemie.agents.tools.interactive.request_user_input import RequestUserInputTool
from codemie.core.a2ui import catalog
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import AssistantChatRequest
from codemie.rest_api.models.conversation import GeneratedMessage
from codemie.service.conversation.a2ui_intake import (
    materialize_a2ui_action_text,
    materialize_a2ui_request_text,
    validate_a2ui_intake,
)

SURFACE = [
    {"id": "root", "component": "Column", "children": ["q", "email", "langs", "due", "ok"]},
    {"id": "q", "component": "Text", "text": "Tell us about yourself"},
    {
        "id": "email",
        "component": "TextField",
        "label": "Email",
        "value": {"path": "/email"},
        "variant": "shortText",
        "validationRegexp": r"[^@\s]+@[^@\s]+\.[^@\s]+",
    },
    {
        "id": "langs",
        "component": "ChoicePicker",
        "label": "Languages",
        "value": {"path": "/langs"},
        "variant": "multipleSelection",
        "options": [
            {"label": "Python", "value": "py"},
            {"label": "TypeScript", "value": "ts"},
            {"label": "Go", "value": "go"},
        ],
    },
    {
        "id": "due",
        "component": "DateTimeInput",
        "label": "Available from",
        "value": {"path": "/due"},
        "enableDate": True,
        "min": "2026-01-01T00:00:00",
        "max": "2026-12-31T00:00:00",
    },
    {"id": "ok", "component": "Button", "child": "ok-label", "action": {"event": {"name": "submit"}}},
    {"id": "ok-label", "component": "Text", "text": "Send"},
]

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))
    print(f"{'PASS' if condition else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")


# 1. The agent authors a surface; the tool validates and streams it, then ends the turn.
generator = MagicMock()
tool = RequestUserInputTool(thread_generator=generator)
returned = tool.execute(components=SURFACE, data_model={"email": "", "langs": [], "due": ""})
chunks = [json.loads(call.args[0]) for call in generator.send.call_args_list]
envelopes = [chunk["a2ui"] for chunk in chunks]

check("tool ends the turn with no text (return_direct)", returned == "" and tool.return_direct)
check(
    "one envelope per chunk, carried under the 'a2ui' key and no text content",
    all(c.get("a2ui") and c.get("generated_chunk") is None and c.get("generated") is None for c in chunks),
)
check(
    "envelope order is createSurface -> updateComponents -> updateDataModel",
    [next(iter(e for e in env if e != "version")) for env in envelopes]
    == ["createSurface", "updateComponents", "updateDataModel"],
)
surface_id = envelopes[0]["createSurface"]["surfaceId"]
check(
    "surface advertises the official catalog id",
    envelopes[0]["createSurface"]["catalogId"] == catalog.CATALOG_ID,
    catalog.CATALOG_ID,
)

# 2. The client declares its catalog support on the wire, exactly as the frontend sends it.
request = AssistantChatRequest.model_validate({"text": "hi", "a2uiSupportedCatalogs": [catalog.CATALOG_ID]})
check(
    "capability arrives from the wire payload the frontend sends",
    request.a2ui_supported_catalogs == [catalog.CATALOG_ID],
)

# 3. The user answers. The action envelope is shaped as the renderer submits it.
history = [GeneratedMessage(role="Assistant", message="", history_index=0, a2ui_envelopes=envelopes)]
action = {
    "version": "v0.9.1",
    "action": {"name": "submit", "surfaceId": surface_id, "sourceComponentId": "ok"},
}
answer = {"email": "ada@example.com", "langs": ["py", "ts"], "due": "2026-06-01T00:00:00"}
validate_a2ui_intake(history, action, answer)
check("a well-formed answer is accepted", True)


# 4. Tampering is refused on the server, whatever the client sent.
def rejected(label, act, model):
    try:
        validate_a2ui_intake(history, act, model)
    except ExtendedHTTPException as exc:
        check(f"rejects {label}", True, str(exc.message)[:70])
    else:
        check(f"rejects {label}", False, "accepted")


rejected("an option the surface never offered", action, {**answer, "langs": ["rust"]})
rejected("a value outside the declared date range", action, {**answer, "due": "2027-06-01T00:00:00"})
rejected("a timezone-aware date smuggled past the range", action, {**answer, "due": "2027-06-01T00:00:00Z"})
rejected("a value failing the declared regexp", action, {**answer, "email": "not-an-email"})
rejected("a data-model key the surface never bound", action, {**answer, "smuggled": "x"})
rejected(
    "an action name no Button offers",
    {"version": "v0.9.1", "action": {"name": "escalate", "surfaceId": surface_id}},
    answer,
)
rejected(
    "an action for a surface that does not exist",
    {"version": "v0.9.1", "action": {"name": "submit", "surfaceId": "00000000-0000-0000-0000-000000000000"}},
    answer,
)
rejected(
    "a smuggled catalogId in the action envelope",
    {"version": "v0.9.1", "action": {"name": "submit", "surfaceId": surface_id}, "catalogId": "evil"},
    answer,
)

# 5. Both turns are materialized into the text the model reads next turn.
request_text = materialize_a2ui_request_text("", envelopes)
action_text = materialize_a2ui_action_text("email: ada@example.com", action, answer)
check("the question is replayed to the model", "Tell us about yourself" in request_text, request_text[:60])
check(
    "the structured answer is replayed to the model",
    "ada@example.com" in action_text and "submit" in action_text,
    action_text[:80],
)

# 6. The agent cannot author a surface the user has no way to answer.
try:
    RequestUserInputTool(thread_generator=MagicMock()).execute(
        components=[
            {"id": "root", "component": "Column", "children": ["t"]},
            {"id": "t", "component": "TextField", "label": "Name", "value": {"path": "/n"}},
        ]
    )
except ValueError as exc:
    check("refuses to emit a surface with no submit control", "no Button" in str(exc), str(exc)[:70])
else:
    check("refuses to emit a surface with no submit control", False, "emitted")

failed = [name for name, ok, _ in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
if failed:
    raise SystemExit("FAILED: " + "; ".join(failed))
