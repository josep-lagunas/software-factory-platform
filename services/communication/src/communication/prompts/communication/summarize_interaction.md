# Summarize interaction

Given the prior durable summary of a user interaction and the current
inbound message, produce the updated durable summary of the interaction
(MAS §9.4 Summary; AP-009).

Rules:

- The summary is the durable representation of the interaction — NEVER a
  conversation transcript. Do not quote message exchanges.
- Carry forward every fact from the prior summary that remains relevant;
  fold in the current message.
- Keep the summary concise, factual, and in English BY DEFAULT. If the
  current message explicitly asks for the summary in another language
  (e.g. "resumeix-ho en català", "résponds en français", "auf Deutsch"),
  write the summary in THAT language instead, and keep writing it in that
  language in subsequent summaries of the same interaction.
- Additionally, classify the CURRENT message: does it express confirmation
  intent — the user accepting the summary as-is (a confirmation word such
  as "confirm", "confirmo", "d'acord", "confirmez", "bestätige", a clear
  "yes, that's it"), possibly with a polite preamble)? Set
  `is_confirmation_intent` to `true` ONLY when the message's primary
  intent is to confirm. A message that asks for any change, addition, or
  clarification — even one that also sounds positive — is NOT a
  confirmation: set `is_confirmation_intent` to `false`.
- Respond with strict JSON only:
  `{"summary": "<non-empty summary text>", "is_confirmation_intent": <true|false>}`.
