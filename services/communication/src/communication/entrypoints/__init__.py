"""Entrypoints of the communication service (the HTTP/process edge).

Empty by design (SFP-132 re-scope, ID-076): ALL inbound HTTP ingress belongs
to the External Events Service (MAS §9.2) — the sole webhook route is
``/webhooks/{endpoint_id}`` (SFP-120). The drifted Slack Events receiver of
PR #163 (``slack_events_endpoint`` / ``dev_slack_events``) is removed;
Communication's inbound half is the bus consumer in
:mod:`communication.interfaces.slack_inbound`, and its local dev serving is
the external-events dev webhook runner. The package remains so the uniform
five-layer service layout (SFP-24 / Impl Notes §3) stays intact.
"""
