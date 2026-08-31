# V1 Store Decomposition

## Objective

Reduce `V1Store` to a compatibility facade over explicit persistence and domain-service boundaries while preserving the V1 API, SQLite schema, transactions, and Runtime behavior.

## Dependency direction

```text
API / Runtime / Capabilities
            |
         V1Store facade
       /        |        \
Repositories  Services  SQLite infrastructure
                   |
              Repositories
```

Repositories own SQL persistence for one data family. Services own decisions and stateful coordination. The facade preserves existing method names while callers migrate incrementally.

## First extraction

- `repositories/project_repository.py`: Project and long-lived Conversation Agent records.
- `repositories/artifact_repository.py`: Artifact persistence.
- `repositories/event_repository.py`: Event persistence and correlation queries.
- `repositories/schema_repository.py`: SQLite schema creation, migrations, and persisted-status validation.
- `services/scheduler.py`: admission, dependency resolution, WIP/resource eligibility, Task claiming, and lease renewal.
- `services/mailbox.py`: durable message creation, delivery/receipt/acknowledgement/failure transitions, delivery-attempt evidence, and explicit redelivery.

`V1Store` retains connection/transaction ownership and delegates these operations. A repository or service does not import `V1Store`; it depends on a narrow host protocol, preventing a circular module dependency.

## Compatibility boundary

- Existing `V1Store` public methods and return shapes remain unchanged.
- SQLite table names and columns remain unchanged.
- A single transaction continues to use one SQLite connection.
- Runtime state transitions continue to use `app/v1/states.py`.
- No compatibility fallback or duplicate implementation is retained after a method is migrated.

## Later extraction

After the first extraction is stable, the remaining areas can move independently: Workflow repository, Conversation repository, Task repository, Run repository, Projection service, Governance repository, and Execution Receipt repository.
