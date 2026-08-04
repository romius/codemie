# Work Item: EPMCDME-13264

**Ticket**: https://jiraeu.epam.com/browse/EPMCDME-13264  
**Type**: Bug  
**Status**: In Progress  
**Assignee**: Oleksandr Cherevach  

## Summary

Personal LiteLLM key requests incorrectly send user_id header and cause wrong budget assignment.

## Complexity

Initial assessment: M (17/36)

## Acceptance Criteria

- Requests using a personal LiteLLM key do not send a populated `user_id` header (`model_kwargs["user"]`).
- Budget attribution for personal LiteLLM key requests is correct.
- Existing flows that rely on `user_id` for non-personal key scenarios continue to work as expected.
- Regression coverage confirms correct behavior for both personal and non-personal LiteLLM key usage.
