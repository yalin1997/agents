# Core contracts

M0 defines the framework-neutral boundary used by later runtime, provider,
tool, session, context, command, and extension work. The objects in
`pi_deepagent.contracts` contain plain Python values and do not depend on
LangChain or Deep Agents message classes.

## Domain objects

- `ModelRef(provider, model_id, thinking_level)` identifies a model without
  selecting a provider SDK. Its canonical display form is
  `provider/model_id`; the existing factory model strings such as
  `provider:model` remain supported and are not silently rewritten.
- `ToolCall(call_id, name, arguments)` contains a named tool request and a
  JSON-compatible argument object.
- `AgentRun(run_id, session_id, model, workspace, started_at, parent_run_id,
  metadata)` records run metadata.
- `AgentEvent(event_id, run_id, kind, payload, occurred_at, sequence)` is a
  sequenced, extensible event. `kind` is a string so extensions can add event
  names without changing the core enum.
- `RunResult(run_id, status, output, events, error_code, error, metadata)` is
  the terminal result. Status values are `completed`, `aborted`, and `failed`.

## Serialization

Each object exposes `to_dict()` and the objects with nested records expose a
matching `from_dict()`. `RunResult` additionally exposes `to_json()` and
`from_json()`. The JSON shape uses snake-case keys, UTC ISO-8601 timestamps
with a `Z` suffix, strings for identifiers, arrays for event collections, and
explicit `null` values for optional fields. JSON encoding is deterministic
(UTF-8, sorted keys, compact separators) so contract fixtures can be
replayed. Mapping inputs are copied at construction; non-JSON values are
rejected before persistence adapters receive them.

## Compatibility and boundaries

`create_pi_deepagent` retains its existing arguments and return value. Built-in
tools remain first and ordered `pi_read`, `pi_write`, `pi_edit`, `pi_bash`;
`extra_tools` are appended and `subagents` is passed through unchanged.
`ApplicationService` owns interactive conversation history so frontends do not
mutate a shared list themselves. The provider, tool, session, context,
extension, command, and runtime protocols are intentionally small; concrete
registries and persistence are added in later milestones.

`pi_deepagent.errors` provides stable error codes for validation, provider,
tool, session, extension, command, runtime, and internal failures. Error
details are structured data and must not contain credentials or other secrets.
