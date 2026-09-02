# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""History materialization for A2UI surfaces and structured user actions.

Envelopes are persisted raw in the conversation
history, and these helpers render them into deterministic text
replayed to the LLM so the agent's next turn keeps the context of its own question
and the user's structured answer.
"""

import json
from datetime import datetime, time, timezone
from typing import Any, Iterable, Optional

from codemie.core.a2ui import envelopes as wire
from codemie.core.a2ui.catalog import validate_action_envelope
from codemie.core.a2ui.config import (
    CREATE_SURFACE,
    MAX_ACTION_NAME_LEN,
    MAX_BINDING_DEPTH,
    MAX_FIELD_VALUE_LEN,
    MAX_PAYLOAD_BYTES,
)
from codemie.core.exceptions import ExtendedHTTPException

_UNKNOWN_SURFACE_ID = "unknown"


def _extract_surface_id(envelopes: list[dict[str, Any]]) -> str:
    """Pull the surfaceId out of the first envelope payload that carries one."""
    for envelope in envelopes:
        if not isinstance(envelope, dict):
            continue
        for payload in envelope.values():
            if isinstance(payload, dict) and payload.get("surfaceId"):
                return str(payload["surfaceId"])
    return _UNKNOWN_SURFACE_ID


def _action_surface_id(action_envelope: Any) -> Optional[str]:
    """Extract action.surfaceId from a wire envelope; None when structurally absent."""
    if not isinstance(action_envelope, dict):
        return None
    action = action_envelope.get("action")
    if not isinstance(action, dict):
        return None
    surface_id = action.get("surfaceId")
    return surface_id if isinstance(surface_id, str) and surface_id else None


def _envelopes_create_surface(envelopes: Any, surface_id: str) -> bool:
    if not isinstance(envelopes, list):
        return False
    return any(
        isinstance(envelope, dict)
        and isinstance(envelope.get(CREATE_SURFACE), dict)
        and envelope[CREATE_SURFACE].get("surfaceId") == surface_id
        for envelope in envelopes
    )


def _invalid(message: str) -> ExtendedHTTPException:
    return ExtendedHTTPException(code=422, message=message)


def _button_event_names(components: list[dict[str, Any]]) -> set[str]:
    """Action names the surface actually offers (Button ``action.event.name``).

    A Modal's trigger contributes nothing: its click opens the dialog rather than
    submitting, and the emitter supplies its action only because the catalog requires the
    property. Counting it would make a name the user can never send a name the server
    accepts — including the synthetic one, which no button in the form can produce.
    """
    triggers = {
        component["trigger"]
        for component in components
        if component.get("component") == "Modal" and isinstance(component.get("trigger"), str)
    }
    names: set[str] = set()
    for component in components:
        if component.get("component") != "Button" or component.get("id") in triggers:
            continue
        action = component.get("action")
        event = action.get("event") if isinstance(action, dict) else None
        name = event.get("name") if isinstance(event, dict) else None
        if isinstance(name, str) and name:
            names.add(name)
    return names


def _data_model_bindings(components: list[dict[str, Any]]) -> dict[tuple[str, ...], dict[str, Any]]:
    """Every data-model path the surface binds, keyed by path segments, with its component.

    A component binds via ``value: {"path": "/<a>/<b>"}``. Nested paths are kept whole:
    the submitted subtree is walked down to the bound leaf, so an unbound branch of a
    nested key cannot slip past the per-component checks. Paths deeper than
    ``MAX_BINDING_DEPTH`` are ignored (and therefore unanswerable) so that walking
    client input stays bounded.
    """
    bindings: dict[tuple[str, ...], dict[str, Any]] = {}
    for component in components:
        value = component.get("value")
        path = value.get("path") if isinstance(value, dict) else None
        if not isinstance(path, str):
            continue
        segments = tuple(segment for segment in path.split("/") if segment)
        if segments and len(segments) <= MAX_BINDING_DEPTH:
            bindings[segments] = component
    return bindings


def _binds_under(bindings: dict[tuple[str, ...], dict[str, Any]], path: tuple[str, ...]) -> bool:
    """True when the surface binds ``path`` itself or anything below it."""
    return any(bound[: len(path)] == path for bound in bindings)


def _validate_bound_subtree(
    bindings: dict[tuple[str, ...], dict[str, Any]],
    path: tuple[str, ...],
    value: Any,
) -> None:
    """Validate one submitted node: a bound leaf directly, an interior node by descent."""
    key = "/".join(path)
    component = bindings.get(path)
    if component is not None:
        _validate_bound_value(component, key, value)
        return
    # No component owns this exact path, but something below it is bound (the caller
    # checked): the node may only carry the branches the surface actually declared.
    if not isinstance(value, dict):
        raise _invalid(f"Field '{key}' is not bound by the surface")
    for child_key, child_value in value.items():
        child_path = (*path, str(child_key))
        if not _binds_under(bindings, child_path):
            raise _invalid(f"Unknown A2UI data model key: {'/'.join(child_path)!r}")
        _validate_bound_subtree(bindings, child_path, child_value)


def _parse_iso_moment(raw: Any):
    """Parse an ISO 8601 date, date-time or time string; None when unparseable."""
    if not isinstance(raw, str) or not raw:
        return None
    text = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
    for parse in (datetime.fromisoformat, time.fromisoformat):
        try:
            return parse(text)
        except ValueError:
            continue
    return None


def _as_utc(moment):
    """Read a naive date-time/time as UTC so both sides of a bound check are comparable."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def _validate_choice_value(component: dict[str, Any], key: str, value: Any) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _invalid(f"Field '{key}' must be a list of strings")
    declared = component.get("options")
    options = (
        {option.get("value") for option in declared if isinstance(option, dict)}
        if isinstance(declared, list)
        else set()
    )
    unknown = sorted({item for item in value if item not in options})
    if unknown:
        raise _invalid(f"Field '{key}' has values the surface never offered: {unknown}")
    # The catalog's default variant is "mutuallyExclusive", so an omitted variant is
    # single-select too — capping only on the explicit value would let a crafted answer
    # submit several selections for a single-choice component.
    if component.get("variant", "mutuallyExclusive") == "mutuallyExclusive" and len(value) > 1:
        raise _invalid(f"Field '{key}' accepts at most one selection, got {len(value)}")


def _validate_text_value(component: dict[str, Any], key: str, value: Any) -> None:
    # The "number" variant is a numeric input, and a renderer that writes a real number for
    # it is behaving correctly — demanding a string here rejected forms the user had filled
    # in properly. `bool` is excluded explicitly: it is an `int` subclass in Python, so a
    # checkbox answer would otherwise sail through as a number.
    if component.get("variant") == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return
    if not isinstance(value, str):
        raise _invalid(f"Field '{key}' must be a string")
    if len(value) > MAX_FIELD_VALUE_LEN:
        raise _invalid(f"Field '{key}' value too long (max {MAX_FIELD_VALUE_LEN})")


def _validate_datetime_value(component: dict[str, Any], key: str, value: Any) -> None:
    if not isinstance(value, str):
        raise _invalid(f"Field '{key}' must be an ISO 8601 date/time string")
    if not value:
        return  # cleared field
    moment = _parse_iso_moment(value)
    if moment is None:
        raise _invalid(f"Field '{key}' must be an ISO 8601 date/time string")
    for bound, relation in (("min", "on or after"), ("max", "on or before")):
        limit = _parse_iso_moment(component.get(bound))
        if limit is None:
            continue
        # The VALUE side is client-controlled, so a type mismatch cannot be waved through
        # as an authoring quirk: a bare "12:30" parses as a time, compares with neither
        # end of a date range, and would otherwise turn both bounds off at once.
        if type(limit) is not type(moment):
            raise _invalid(f"Field '{key}' must be the same kind of date/time value as its declared {bound}")
        # Both sides are normalized to UTC first: skipping the check when only one side
        # carries an offset would let a single "Z" turn the declared bound off.
        instant, threshold = _as_utc(moment), _as_utc(limit)
        out_of_range = instant < threshold if bound == "min" else instant > threshold
        if out_of_range:
            raise _invalid(f"Field '{key}' must be {relation} {component[bound]}")


def _validate_slider_value(component: dict[str, Any], key: str, value: Any) -> None:
    # `bool` is an `int` subclass in Python, so it is excluded explicitly or a checkbox
    # answer would sail through as a slider position.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid(f"Field '{key}' must be a number")
    for bound, relation in (("min", "at least"), ("max", "at most")):
        limit = component.get(bound)
        if isinstance(limit, bool) or not isinstance(limit, (int, float)):
            continue
        if (value < limit) if bound == "min" else (value > limit):
            raise _invalid(f"Field '{key}' must be {relation} {limit}")


def _validate_bound_value(component: dict[str, Any], key: str, value: Any) -> None:
    """Validate one submitted value against the component the surface bound to that key.

    Every Basic Catalog component that can declare ``value`` is handled here — CheckBox,
    ChoicePicker, DateTimeInput, Slider, TextField — so the fallback is not "no contract
    to check" but "this surface bound something that cannot be bound". That is either a
    forged answer or a catalog addition nobody taught this function about, and both are
    safer refused than waved through into the prompt.
    """
    name = component.get("component")
    if name == "ChoicePicker":
        _validate_choice_value(component, key, value)
    elif name == "TextField":
        _validate_text_value(component, key, value)
    elif name == "CheckBox":
        if not isinstance(value, bool):
            raise _invalid(f"Field '{key}' must be a boolean")
    elif name == "DateTimeInput":
        _validate_datetime_value(component, key, value)
    elif name == "Slider":
        _validate_slider_value(component, key, value)
    else:
        raise _invalid(f"Field '{key}' is bound to a component that accepts no answer")


def _validate_answer_against_surface(
    surface_envelopes: list[dict[str, Any]],
    action_envelope: dict[str, Any],
    data_model: Optional[dict[str, Any]],
) -> None:
    """Re-validate the submitted answer against the surface the server itself stored.

    Client-side validation is not trusted: without this, a client could submit action
    names and values the surface never offered and have them replayed into the LLM
    prompt under the trusted "[Structured response to surface ...]" framing.
    """
    components = wire.components_of(surface_envelopes)
    action = action_envelope.get("action")
    action_name = action.get("name") if isinstance(action, dict) else None
    # Capped before any surface lookup: the name is echoed into the prompt, and the
    # surface that "offers" it is agent-authored, so it cannot license arbitrary text.
    if isinstance(action_name, str) and len(action_name) > MAX_ACTION_NAME_LEN:
        raise _invalid(f"Action name too long (max {MAX_ACTION_NAME_LEN})")
    event_names = _button_event_names(components)
    # Only Button carries an action in the Basic Catalog, so a surface that declares no
    # Button action.event offers no way to answer it: such an action is client-forged.
    if not event_names:
        raise _invalid("Surface offers no action to submit")
    if action_name not in event_names:
        raise _invalid(f"Action name {action_name!r} is not offered by surface")
    if not data_model:
        return
    if not isinstance(data_model, dict):
        raise _invalid("A2UI data model must be an object")
    bindings = _data_model_bindings(components)
    for key, value in data_model.items():
        path = (str(key),)
        if not _binds_under(bindings, path):
            raise _invalid(f"Unknown A2UI data model key: {key!r}")
        # `null` is not a value any component offers — emptiness is "" / [] / false — so
        # it is validated like any other value and rejected by the component's type rule.
        _validate_bound_subtree(bindings, path, value)


def unanswerable_seed_paths(
    surface_envelopes: list[dict[str, Any]],
    data_model: Optional[dict[str, Any]],
) -> list[str]:
    """Seeded data-model paths this very intake would refuse if they came back.

    The client echoes the whole stored data model on every submit, so a seed the intake
    rejects makes the form unanswerable forever — no retry clears it, because the seed is
    part of the stored surface. The tool calls this before emitting and drops what it
    names, which is why the check lives here rather than being restated in the catalog:
    one set of rules, applied in both directions.
    """
    if not isinstance(data_model, dict) or not data_model:
        return []
    bindings = _data_model_bindings(wire.components_of(surface_envelopes))
    refused: list[str] = []
    for key, value in data_model.items():
        path = (str(key),)
        if not _binds_under(bindings, path):
            refused.append(str(key))
            continue
        try:
            _validate_bound_subtree(bindings, path, value)
        except ExtendedHTTPException:
            refused.append(str(key))
    return refused


def _reject_a_second_answer(message: Any, surface_id: str, replacing_history_index: Optional[int]) -> None:
    """Refuse an answer to a surface this history already records an answer for.

    Editing a previous request is the one exception: with ``replacing_history_index`` set,
    the answer stored at that exact turn is being overwritten and does not count. The index
    comes from the client, so the match is strict equality on the stored answer's own
    ``history_index``, never ``>=`` — otherwise sending 0 would mark every earlier answer
    as "being replaced" and walk a duplicate straight past the once-only guard.
    """
    stored_action = getattr(message, "a2ui_action", None)
    if stored_action is None or _action_surface_id(stored_action) != surface_id:
        return
    message_index = getattr(message, "history_index", None)
    being_replaced = (
        replacing_history_index is not None and message_index is not None and message_index == replacing_history_index
    )
    if not being_replaced:
        raise ExtendedHTTPException(code=422, message="Surface already answered")


def validate_a2ui_intake(
    history: Iterable,
    action_envelope: dict[str, Any],
    data_model: Optional[dict[str, Any]] = None,
    replacing_history_index: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Validate an incoming A2UI action against the conversation history.

    Checks the surface exists, is unanswered, fits the size cap, and that its action name
    and data model match the surface the SERVER stored — client validation is not trusted.
    Raises ExtendedHTTPException(422) on any violation, and returns the stored envelopes so
    the caller resolves the catalog from our own record rather than from the client.

    Re-answering mirrors editing a previous request: with ``replacing_history_index`` set,
    the answer at that exact turn is being overwritten and does not count as answered. The
    index comes from the client, so the match is strict equality on the stored answer's own
    ``history_index``, never ``>=`` — otherwise sending 0 would mark every prior answer as
    "being replaced" and slip a duplicate past the once-only guard.
    """
    surface_id = _action_surface_id(action_envelope)
    if surface_id is None:
        raise ExtendedHTTPException(code=422, message="Invalid A2UI action envelope")
    payload_size = len(json.dumps(action_envelope))
    if data_model is not None:
        payload_size += len(json.dumps(data_model))
    if payload_size > MAX_PAYLOAD_BYTES:
        raise ExtendedHTTPException(code=422, message=f"Payload too large (max {MAX_PAYLOAD_BYTES} bytes)")
    surface_envelopes = None
    for message in history:
        stored_envelopes = getattr(message, "a2ui_envelopes", None)
        if stored_envelopes and _envelopes_create_surface(stored_envelopes, surface_id):
            surface_envelopes = stored_envelopes
        _reject_a_second_answer(message, surface_id, replacing_history_index)
    if surface_envelopes is None:
        raise ExtendedHTTPException(code=422, message="Unknown A2UI surface")
    errors = validate_action_envelope(action_envelope)
    if errors:
        raise ExtendedHTTPException(code=422, message=f"Invalid A2UI action: {'; '.join(errors)}")
    _validate_answer_against_surface(surface_envelopes, action_envelope, data_model)
    return surface_envelopes


def _describe_component(component: dict[str, Any]) -> str:
    """One line per component: what it is, what it said, what it was bound to."""
    parts = [f"{component.get('id')}: {component.get('component')}"]
    label = component.get("label") or component.get("text")
    if isinstance(label, str) and label:
        parts.append(f'"{label[:80]}"')
    value = component.get("value")
    if isinstance(value, dict) and isinstance(value.get("path"), str):
        parts.append(f"-> {value['path']}")
    options = component.get("options")
    if isinstance(options, list) and options:
        offered = [str(o.get("value")) for o in options if isinstance(o, dict)][:8]
        parts.append(f"({', '.join(offered)})")
    action = component.get("action")
    if isinstance(action, dict):
        event = action.get("event")
        if isinstance(event, dict) and event.get("name"):
            parts.append(f"action={event['name']}")
    return " ".join(parts)


def materialize_a2ui_request_text(display_text: str, envelopes: list[dict[str, Any]]) -> str:
    """Deterministic text describing the A2UI surface the assistant showed the user.

    The ``request_user_input`` tool returns "" (return_direct), so the assistant message
    is empty; this replays what was asked so the resumed turn keeps its context,
    symmetric to ``materialize_a2ui_action_text`` on the answer side.

    Deliberately NOT the raw component JSON, which is what this used to be. That put a
    6 kB JSON array in the history AS THE ASSISTANT'S OWN PREVIOUS MESSAGE, and the model
    did the obvious thing on the next turn: it answered in the same shape, printing the
    next surface as JSON in its reply instead of calling the tool. Observed live — the
    first surface of a conversation always worked, the second never did. A summary carries
    the same information for reasoning about the answer, cannot be imitated into a broken
    reply, and costs a fraction of the tokens on every later turn.
    """
    surface_id = _extract_surface_id(envelopes)
    components = wire.components_of(envelopes)
    lines = [f"[Interactive surface {surface_id} shown to the user]"]
    lines += [f"- {_describe_component(component)}" for component in components]
    materialized = "\n".join(lines)
    return f"{display_text}\n\n{materialized}" if display_text else materialized


def materialize_a2ui_action_text(
    display_text: str,
    action_envelope: dict[str, Any],
    data_model: Optional[dict[str, Any]] = None,
) -> str:
    """Deterministic structured text replayed to the LLM for an A2UI user action."""
    action = action_envelope.get("action") if isinstance(action_envelope, dict) else None
    action = action if isinstance(action, dict) else {}
    surface_id = str(action.get("surfaceId") or _UNKNOWN_SURFACE_ID)
    payload: dict[str, Any] = {"action": action}
    if data_model is not None:
        payload["dataModel"] = data_model
    materialized = (
        f"[Structured response to surface {surface_id}]\n"
        f"{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
    )
    return f"{display_text}\n\n{materialized}" if display_text else materialized
