# Teams Bot Handoff — EPMCDME-14111 (billing routing for group chats, "option 4b")

Status: implemented on this backend branch (not yet merged to `main`). This doc is for the
`codemie-teams-bot` service — it explains the one new header the bot must send and exactly
what it does and doesn't affect.

## Problem this solves

In a Teams group chat, every message to CodeMie is authenticated as the bot's own service
account (`codemie-teams-bot`), not as the individual end-user typing in the channel. Without
help from the bot, every assistant call — and every dollar of usage — would be billed to the
shared service account instead of the actual end-user.

## What to send

Add one header to `POST /v1/assistants/{assistant_id}/model`:

```
X-Teams-Sender-Email: <end-user's email>
```

- Send the Teams end-user's email exactly as CodeMie has it registered (case doesn't need
  special handling — lookup is by the DB's stored email).
- Omit the header (or send it empty) for any call that should stay billed to the bot itself
  (e.g. bot-initiated housekeeping, not an actual user message). No header → no behavior
  change from today.

Everything else about the call — auth (the bot's own token/credentials), the assistant id,
the request body — is unchanged.

## What the header does

- **Only takes effect when the caller is the bot itself.** The backend checks that the
  authenticated caller's id exactly matches the allow-listed service account
  (`codemie-teams-bot`) *and* that the caller's `user_type` is `service_account`. If some
  other caller sends this header, it is silently ignored (logged, not an error) and billing
  stays on that caller — this header cannot be used to bill to an arbitrary user from a
  non-bot caller.
- **When it takes effect:** the backend looks up a CodeMie user by `sender_email` and routes
  **billing only** to that user — specifically: usage-record accounting, request-summary
  attribution, and budget-key selection (which budget/limit is checked and decremented).
- **Access control is unaffected.** Whether the request is allowed to use the target
  assistant at all is always evaluated against the bot's own identity (the original caller),
  never against the resolved end-user. In practice this means: the bot's service account
  itself needs access to any assistant it forwards Teams messages to (e.g. via a shared/
  global assistant, or explicit project membership) — granting an end-user access to an
  assistant does **not** implicitly grant the bot's forwarded calls access to it.

## Error behavior

If `X-Teams-Sender-Email` is sent by the allow-listed bot caller but doesn't resolve to a
usable billing identity, the endpoint returns `404`:

- No CodeMie user has that email at all.
- The matched user is inactive, soft-deleted, or is itself a service account (a service
  account can't be used as a billing target — prevents a Teams message from ever being
  billed to another automation identity by mistake).

In both cases the response is the standard `ExtendedHTTPException` shape (`message`,
`details`, `help`). Recommended bot-side handling: surface a clear "your CodeMie account
isn't set up for billing" style message to the Teams user rather than retrying — retrying
with the same email will hit the same 404.

## Non-goals / things that did NOT change

- No new endpoint — this rides on the existing `POST /v1/assistants/{assistant_id}/model`.
- No change to how the bot authenticates itself.
- No change to response shape/streaming behavior of that endpoint.
- The header is a pure additive opt-in: any existing bot traffic that never sends it keeps
  behaving exactly as before (billed to the bot's own service account).
