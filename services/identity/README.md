# identity

Uniform per-service layout (Implementation Notes §3).

```
src/identity/
    domain/          # aggregates, entities, value objects, domain events, invariants
    application/     # command/event/query handlers, use cases, transaction boundaries
    interfaces/      # HTTP / message adapters, transport concerns
    infrastructure/  # I/O: DB, messaging clients, external services
    entrypoints/     # process bootstrap, DI composition root, app startup
```

**Dependency direction:** dependencies flow inward — `entrypoints → domain`.
`domain/` has **no infrastructure imports** (no DB, no messaging). Outer layers
(`interfaces`, `infrastructure`, `entrypoints`) depend on inner layers; the
domain depends only on the standard library and shared kernel packages
(e.g. `sfp-contracts`).
