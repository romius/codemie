import json
import logging
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(level=logging.DEBUG, format="[mock] %(message)s")
log = logging.getLogger(__name__)

seen_rs_ids: set = set()


class MockAzureResponsesHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send_json(self, s, b):
        d = json.dumps(b).encode()
        self.send_response(s)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(d)))
        self.end_headers()
        self.wfile.write(d)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        body = json.loads(raw) if raw else {}
        log.debug("=== POST %s ===\n%s", self.path, json.dumps(body, indent=2))

        streaming = body.get("stream", False)
        prev_id = body.get("previous_response_id")
        log.debug("previous_response_id=%s", prev_id)

        for item in body.get("input", []):
            if not isinstance(item, dict):
                continue
            item_id = item.get("id", "")
            # Reject rs_* item references (classic store=false case)
            if item_id.startswith("rs_") and item_id in seen_rs_ids:
                log.debug("REJECTING rs_* reference: %s", item_id)
                msg = f"Item with id '{item_id}' not found. " "Items are not persisted when `store` is set to false."
                self.send_json(
                    400,
                    {"error": {"message": msg, "type": "invalid_request_error", "param": "input", "code": None}},
                )
                return
            # Reject encrypted reasoning items — store=false means Azure can't decrypt them
            if item.get("type") == "reasoning" and isinstance(item.get("encrypted_content"), str):
                fake_id = f"rs_{uuid.uuid4().hex[:48]}"
                log.debug("REJECTING encrypted reasoning item (store=false, can't decrypt)")
                msg = (
                    f"Item with id '{fake_id}' not found. "
                    "Items are not persisted when `store` is set to false. "
                    "Try again with `store` set to true, or remove this item from your input."
                )
                self.send_json(
                    400,
                    {"error": {"message": msg, "type": "invalid_request_error", "param": "input", "code": None}},
                )
                return

        resp_id = f"resp_{uuid.uuid4().hex[:32]}"
        rs_id = f"rs_{uuid.uuid4().hex[:48]}"
        seen_rs_ids.add(rs_id)
        msg_id = f"msg_{uuid.uuid4().hex[:32]}"
        model = body.get("model", "gpt-5-mini")
        created = int(time.time())
        text = "Hello! I am a mock response. Send another message to trigger the store=false error."
        log.debug("Returning rs_id=%s streaming=%s", rs_id, streaming)

        # NOTE: Intentionally omit "store" from response — real Azure doesn't echo store:false back,
        # so Codex CLI assumes items ARE persisted and includes rs_* ids in the next request.
        # Fake encrypted_content triggers Codex CLI to include the rs_* id in the next request
        fake_enc = "ZmFrZV9lbmNyeXB0ZWRfY29udGVudF9mb3JfcmVwcm9kdWN0aW9u"  # base64 placeholder

        if streaming:
            events = [
                (
                    "response.created",
                    {
                        "type": "response.created",
                        "response": {
                            "id": resp_id,
                            "object": "response",
                            "created_at": created,
                            "model": model,
                            "status": "in_progress",
                            "output": [],
                            "usage": None,
                        },
                    },
                ),
                (
                    "response.output_item.added",
                    {
                        "type": "response.output_item.added",
                        "output_index": 0,
                        "item": {
                            "type": "reasoning",
                            "id": rs_id,
                            "encrypted_content": fake_enc,
                            "summary": [],
                            "status": "in_progress",
                        },
                    },
                ),
                (
                    "response.output_item.done",
                    {
                        "type": "response.output_item.done",
                        "output_index": 0,
                        "item": {
                            "type": "reasoning",
                            "id": rs_id,
                            "encrypted_content": fake_enc,
                            "summary": [],
                            "status": "completed",
                        },
                    },
                ),
                (
                    "response.output_item.added",
                    {
                        "type": "response.output_item.added",
                        "output_index": 1,
                        "item": {
                            "type": "message",
                            "id": msg_id,
                            "role": "assistant",
                            "content": [],
                            "status": "in_progress",
                        },
                    },
                ),
                (
                    "response.content_part.added",
                    {
                        "type": "response.content_part.added",
                        "item_id": msg_id,
                        "output_index": 1,
                        "content_index": 0,
                        "part": {"type": "output_text", "text": "", "annotations": []},
                    },
                ),
                (
                    "response.output_text.delta",
                    {
                        "type": "response.output_text.delta",
                        "item_id": msg_id,
                        "output_index": 1,
                        "content_index": 0,
                        "delta": text,
                    },
                ),
                (
                    "response.output_text.done",
                    {
                        "type": "response.output_text.done",
                        "item_id": msg_id,
                        "output_index": 1,
                        "content_index": 0,
                        "text": text,
                    },
                ),
                (
                    "response.content_part.done",
                    {
                        "type": "response.content_part.done",
                        "item_id": msg_id,
                        "output_index": 1,
                        "content_index": 0,
                        "part": {"type": "output_text", "text": text, "annotations": []},
                    },
                ),
                (
                    "response.output_item.done",
                    {
                        "type": "response.output_item.done",
                        "output_index": 1,
                        "item": {
                            "type": "message",
                            "id": msg_id,
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": text, "annotations": []}],
                            "status": "completed",
                        },
                    },
                ),
                (
                    "response.completed",
                    {
                        "type": "response.completed",
                        "response": {
                            "id": resp_id,
                            "object": "response",
                            "created_at": created,
                            "model": model,
                            "status": "completed",
                            "output": [
                                {
                                    "type": "reasoning",
                                    "id": rs_id,
                                    "encrypted_content": fake_enc,
                                    "summary": [],
                                    "status": "completed",
                                },
                                {
                                    "type": "message",
                                    "id": msg_id,
                                    "role": "assistant",
                                    "content": [{"type": "output_text", "text": text, "annotations": []}],
                                    "status": "completed",
                                },
                            ],
                            "usage": {
                                "input_tokens": 100,
                                "output_tokens": 30,
                                "total_tokens": 130,
                                "output_tokens_details": {"reasoning_tokens": 10},
                            },
                        },
                    },
                ),
            ]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            for ev, d in events:
                self.wfile.write(f"event: {ev}\ndata: {json.dumps(d)}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        else:
            self.send_json(
                200,
                {
                    "id": resp_id,
                    "object": "response",
                    "created_at": created,
                    "model": model,
                    "status": "completed",
                    "store": False,
                    "output": [
                        {"type": "reasoning", "id": rs_id, "summary": [], "status": "completed"},
                        {
                            "type": "message",
                            "id": msg_id,
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": text, "annotations": []}],
                            "status": "completed",
                        },
                    ],
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 30,
                        "total_tokens": 130,
                        "output_tokens_details": {"reasoning_tokens": 10},
                    },
                },
            )

    def do_GET(self):  # noqa: N802
        self.send_json(200, {"status": "ok"})


HTTPServer(("0.0.0.0", 8765), MockAzureResponsesHandler).serve_forever()
