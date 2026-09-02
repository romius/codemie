# Work Item: EPMCDME-13903

**External Ticket**: https://jiraeu.epam.com/browse/EPMCDME-13903
**Issue Type**: Story
**Status**: In Progress
**Assignee**: Andriy Lukashchuk
**Epic**: EPMCDME-13285 — AI Safety & Guardrails
**Branch**: EPMCDME-13903_chat-tool-call-confirmations

## Summary

Chat tool calls confirmations

## Description

In CodeMie, users can chat with assistants. Some assistants can be configured to require user confirmation before executing tools.

The platform should display a confirmation dialog whenever such an assistant initiates a tool call. The dialog should allow the user to either allow or deny the tool call. After the user makes a decision, the chat flow should continue accordingly.

## Acceptance Criteria

1. When an assistant configured for user tool confirmation initiates a tool call, the platform displays a confirmation dialog to the user.
2. The confirmation dialog provides clear **Allow** and **Deny** actions.
3. If the user selects **Allow**, the platform executes the requested tool call.
4. If the user selects **Deny**, the platform does not execute the requested tool call.
5. After either **Allow** or **Deny**, the chat resumes without requiring the user to restart the conversation.
6. The assistant response flow continues based on the user's decision.
7. The chat UI clearly communicates that the assistant is waiting for user confirmation while the dialog is active.
8. Existing chat behavior is not broken for assistants that do not require user tool confirmation.

## Linked Artifacts

- `docs/superpowers/runs/20260805-1702-main/requirements.md`

## History

| Timestamp | Event | Actor |
|-----------|-------|-------|
| 2026-08-05T14:02:18Z | Work item created (run mirror) from Jira EPMCDME-13903 | sdlc-pipeline/requirements-intake |
