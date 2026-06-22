"""
Action Binding services (Action Catalog Expansion A2+).

Closes the Actionability Gap: of 35 engine signal types only one is executable
today. This package declares, per signal type, whether a REAL executor action from
services/marketplace/action_catalog can be bound — and where it cannot, says so
honestly (no fabricated action, no invented payload). A2 ships only the contract
(ACTION_BINDINGS registry) + the append-only action_binding_audit table. No
execution, no bridge, no promotion, no measurement, no payload builder, no API,
no UI, no AI.
"""
