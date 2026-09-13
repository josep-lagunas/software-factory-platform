# Summarize interaction

Given the prior durable summary of a user interaction and the current
inbound message, produce the updated durable summary of the interaction
(MAS §9.4 Summary; AP-009).

Rules:

- The summary is the durable representation of the interaction — NEVER a
  conversation transcript. Do not quote message exchanges.
- Carry forward every fact from the prior summary that remains relevant;
  fold in the current message.
- Keep the summary concise, factual, and in English.
- Respond with strict JSON only: `{"summary": "<non-empty summary text>"}`.
