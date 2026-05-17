---
Title: ABS-9 — Fix network error when creating a new case
Description: User norman.stephanie@gmail.com in prod hit "Network error: load failed" when creating a new case. Root cause: a 402 on the first /v1/chat after /v1/cases — open_case reserves a credit against the case (session_id=NULL), and the chat route's reserve_credit_for_session ignored that and claimed another available credit, 402'ing users who'd just spent their last credit on the case open. Fix: chat now adopts the case-reserved credit. Also added a try/catch on the frontend openCase fetch so a real network error surfaces instead of being swallowed.
Start Date: 2026-05-17
Merged Into Main: Yes
Date Merged: 2026-05-17
---
