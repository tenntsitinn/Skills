---
name: graph-node-service
description: Apply Graph / Node / Service boundaries, TypedDict state schemas, and mapping-based dispatch when creating or refactoring Python graph workflows such as LangGraph. Use for graphs, nodes, business services, state updates, and routing tables. 图式工作流的分层编码与状态规范；不要求普通应用引入图框架。
---

# Graph / Node / Service Conventions

Graph orchestrates Nodes. A Node reads State and calls one or more Services. Services return business results; the Node converts them into partial state updates for the graph runtime to apply.

Preserve the project's naming and framework. These conventions do not prescribe a business domain, directory layout, or base class. Small workflows may keep multiple responsibilities in one module if their boundaries remain clear.

中文上下文：本 Skill 面向实现，核心是职责和数据边界；不扩展为完整的框架教程或架构审查流程。

## 1. Graph: orchestration

- Give each independent workflow a clear construction entry point, named `build_graph` by default. Return a compiled graph when the framework requires compilation.
- Define nodes, edges, entry points, exits, and conditional routing in Graph. Keep business processing and external requests out of graph assembly.
- Inject Node and Service dependencies through parameters or constructors. Avoid implicitly creating global clients or database connections. Use a typed context container when several dependencies are shared.
- Choose graph construction frequency and instance lifetime according to dependency lifetimes and framework semantics; do not require either per-call construction or global reuse universally.
- Default to partial state updates with static or conditional edges. Introduce `Command`, `Send`, and reducers only when their semantics are needed.
- Use conditional edges to select among fixed destinations, static parallel edges for fixed fan-out, and `Send` for runtime-generated tasks with individual inputs. Keep this dispatch logic in the Graph orchestration layer.
- Allow `Command` as an exception to default Graph routing when a node needs to return both an update and a routing decision. Keep graph control information out of business Services. `Command` does not guarantee transactional atomicity for business side effects. Static edges may still execute alongside its destinations; make that behavior intentional.

中文上下文：默认把业务结果与流程走向分开；`Command` 是允许的控制流例外，不能据此让 Service 决定图中的跳转。

## 2. Node: state adaptation

- Read State, extract business arguments, call one or more Services, and translate their results into partial state updates.
- Return only fields written by this node. Let the runtime apply the update; do not mutate input State or its mutable contents, or return the entire State merely to deliver a result.
- Prefer typed functions. Use a class implementing `__call__` when it needs to hold dependencies or reuse behavior; do not require a common base class.
- Prefer asynchronous nodes for I/O or asynchronous Service calls. Pure computation may remain synchronous; follow the framework's supported execution model.
- Do not execute the next node through an ordinary function call. Let the runtime schedule it through edges or the `Command` exception above.

## 3. Service: business capabilities

- Accept explicit business inputs and return business results. By default, do not accept or import Graph State, or return update dictionaries designed for the graph.
- Implement Services as functions or classes encapsulating algorithms, data processing, or external calls. A Service may compose a local business workflow; multiple internal steps do not require a subgraph.
- Group numerous arguments into a typed business input object when useful. Do not disguise the entire State as a renamed input object.
- Perform business validation in Services. Validate external request structure at the entry boundary or with a dedicated schema; avoid duplicate validation and do not mandate a specific validation library.
- Apply these boundaries to new code by default. Treat an existing graph-specific `service(state)` as an adapter and extract reusable logic within the current task's scope, without unrelated migrations for naming consistency.
- Extract a Service when reuse, side-effect isolation, independent testing, or an explicit lifecycle justifies it. Simple field transformations or computations may remain in a Node.
- Optionally introduce a separate `WorkflowService` when multiple entry points need shared startup and completion handling: prepare initial state, invoke Graph, and format output. This is not a required layer or the definition of every Service. Nodes must not call back into the entry point of their own workflow.

中文上下文：Service 可以实现局部业务流程。是否抽取取决于复用和职责需要，不要求每个 Node 都配置一个 Service。

### Input mutation contract

- Do not mutate Service inputs by default. Passing lists or dictionaries does not copy them; a Service can still mutate State indirectly through shared objects.
- When in-place mutation is necessary, use an explicit name such as `_in_place` and document the mutation scope and sharing between inputs and returned values. Document partial updates on failure when possible; do not promise atomicity the implementation does not provide.
- An in-place function may return `None` to communicate its usage, but a return annotation does not replace a side-effect contract.
- Before calling a mutating Service, the Node must copy the affected levels or pass newly created data that is not shared with State. Do not require deep copies universally: a shallow list copy shares its elements, and a shallow dictionary copy shares nested objects.

## 4. State: types and update semantics

- Store workflow inputs, intermediate results, and progress in State. Keep clients, database connections, configuration objects, and other runtime dependencies outside it. For checkpointing or recovery, use values compatible with the selected serialization mechanism.
- Prefer `TypedDict` for dictionary-based Python workflow state. Preserve compatibility with an existing explicit state model instead of migrating solely to follow this convention.
- Declare mandatory inputs as required fields and progressively produced fields as `NotRequired`; alternatively, use `Required` inside a `total=False` definition.
- `total=False` permits missing keys, not `None` values. Declare `T | None` only when the business contract permits null values.
- Distinguish full State from partial updates. Prefer a separate `total=False` update type or retain an existing explicitly typed update mechanism. Avoid redundant types for every small node, and do not annotate an update missing required fields as full State.
- Keep only genuinely shared fields in base schemas and extend them for specific workflows. Introduce `Generic` only when member types vary within a reused structure; use concrete types for a single workflow.
- Make node and router field preconditions explicit. Report missing outputs required from previous steps instead of hiding them with defaults; use fallback values only when absence is valid business behavior.
- Omitting a field from an update leaves it unchanged. Explicit `None` is not deletion and is valid only when the field type and channel rules permit it.
- Let channel rules define replacement, merge, or accumulation. LangGraph commonly configures reducers through field annotations. Define merge rules or separate fields for concurrent writes; do not assume lists append automatically. Return deltas or complete values according to the reducer to avoid repeated accumulation.
- Match typing syntax and imports to the project's Python version, using `typing_extensions` when needed. `TypedDict` supplies static constraints, not runtime validation.

中文上下文：Node 决定写入哪些字段，图框架按字段规则应用更新。“返回部分更新”不等于所有字段都返回增量，具体取决于覆盖或合并语义。

## 5. Mapping tables: dispatch by key

- Prefer explicit mappings for relationships such as role, type, operation, or state key to handler, strategy, or node. Avoid repetitive `if/elif` dispatch chains.
- Store callables, strategy objects, or factories without executing processing logic while defining the mapping. Prefer stateless functions or factories in global tables; create instances holding request-specific dependencies or mutable state in the appropriate scope.
- Type both keys and values. Prefer `Literal` or enums for fixed key sets, and keep handler signatures compatible.
- Reject unknown keys or use an explicit business fallback. Do not silently ignore them or select an arbitrary handler.
- Let conditional routers return semantic keys and let Graph mappings connect those keys to destinations. Cover every possible routing key; each destination must be a registered node or the framework's termination marker.
- Keep conditionals for ranges, compound predicates, and priority-sensitive rules. Do not replace every branch or simple fixed edge with a table.

## 6. Implementation scope

- Before modifying a workflow, identify state flow, field writers, Service dependencies on State, routing, and merge rules.
- When using framework-specific capabilities, explain the choice and follow the semantics of the project's framework version.
- Make the smallest change justified by the task. Do not split files or introduce subgraphs, schedulers, event buses, persistence, or plugin registries merely to satisfy a template.

## 7. Examples

### 7.1 Layered workflow

This text-processing workflow demonstrates a consistent State, Node, Service, and routing setup. Graph assembly uses LangGraph; adapt it to the project's framework and types.

```python
from collections.abc import Callable
from typing import Literal, NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph

Operation = Literal["trim", "lower"]
Route = Literal["ready", "empty"]

class WorkflowState(TypedDict):
    input_text: str
    operation: Operation
    result: NotRequired[str]

class WorkflowUpdate(TypedDict, total=False):
    result: str

# Service 层：无状态处理器，不依赖图状态。
HANDLERS: dict[Operation, Callable[[str], str]] = {
    "trim": str.strip,
    "lower": str.lower,
}

def transform_text(text: str, operation: Operation) -> str:
    """返回处理结果，不修改输入。"""
    try:
        handler = HANDLERS[operation]
    except KeyError:
        raise ValueError(f"Unsupported operation: {operation!r}") from None
    return handler(text)

# Node 层：提取参数，调用 Service，声明状态更新。
def transform_node(state: WorkflowState) -> WorkflowUpdate:
    result = transform_text(state["input_text"], state["operation"])
    return {"result": result}

def empty_node(state: WorkflowState) -> WorkflowUpdate:
    # 简单状态整理无需另建 Service。
    return {"result": "(empty)"}

# Graph 层：路由只在 transform_node 完成后执行。
def route_after_transform(state: WorkflowState) -> Route:
    if "result" not in state:
        raise ValueError("transform_node must write result before routing")
    return "ready" if state["result"] else "empty"

ROUTES: dict[Route, str] = {"ready": END, "empty": "empty"}

def build_graph():
    graph = StateGraph(WorkflowState)
    graph.add_node("transform", transform_node)
    graph.add_node("empty", empty_node)
    graph.add_edge(START, "transform")
    graph.add_conditional_edges("transform", route_after_transform, ROUTES)
    graph.add_edge("empty", END)
    return graph.compile()
```

### 7.2 Bad / Good: Service and State boundaries

The next three pairs are independent; bad and good snippets are alternatives. Repeated type declarations are omitted. In this pair, `WorkflowState` contains `input_text: str`, and `WorkflowUpdate` allows a partial update to `normalized_text: str`.

Bad: the Service accepts graph State and returns graph fields while the Node only forwards the call. It does not mutate its input, but it still depends on the specific State schema.

```python
def normalize_service(state: WorkflowState) -> WorkflowUpdate:
    return {"normalized_text": state["input_text"].strip()}

def normalize_node(state: WorkflowState) -> WorkflowUpdate:
    return normalize_service(state)
```

Good: the Node adapts fields; the Service accepts business arguments and returns a business result.

```python
def normalize_text(text: str) -> str:
    return text.strip()

def normalize_node(state: WorkflowState) -> WorkflowUpdate:
    result = normalize_text(state["input_text"])
    return {"normalized_text": result}
```

中文上下文：此例突出独立业务能力的接口边界。若只是单处简单转换，直接留在 Node 即可，不必为了模仿示例抽取 Service。

### 7.3 Bad / Good: shared mutable inputs

Assume a non-mutating Service contract. `Item` is a dictionary type with `text: str` and `score: float`; `calculate_score(text)` returns a score without input mutation. State contains `items: list[Item]`, and the partial update type contains `scored_items: list[Item]`.

Bad: the new list shares the original dictionaries, so assigning scores mutates the input.

```python
def score_items(items: list[Item]) -> list[Item]:
    results = list(items)
    for item in results:
        item["score"] = calculate_score(item["text"])
    return results
```

Good: create new dictionaries for the updated top-level scores.

```python
def score_items(items: list[Item]) -> list[Item]:
    return [
        {**item, "score": calculate_score(item["text"])}
        for item in items
    ]

def score_node(state: WorkflowState) -> WorkflowUpdate:
    return {"scored_items": score_items(state["items"])}
```

中文上下文：这里只修改顶层分数，因此复制字典即可。修改嵌套对象时再处理对应层级的共享关系，不一律深拷贝。

### 7.4 Bad / Good: reducer-aware updates

Here `summaries` uses a list-concatenation reducer and starts as `[]` or a list of existing summaries. `summarize_service(text)` returns one new summary.

```python
import operator
from typing import Annotated, TypedDict

class WorkflowState(TypedDict):
    text: str
    summaries: Annotated[list[str], operator.add]

class WorkflowUpdate(TypedDict, total=False):
    summaries: list[str]
```

Bad: the Node returns historical summaries again, so the reducer appends them a second time.

```python
def summarize_node(state: WorkflowState) -> WorkflowUpdate:
    new_summary = summarize_service(state["text"])
    return {"summaries": state["summaries"] + [new_summary]}
```

With an existing value of `["A"]` and a new summary `"B"`, the merged result becomes `["A", "A", "B"]`.

Good: return only the new summary for the reducer to accumulate.

```python
def summarize_node(state: WorkflowState) -> WorkflowUpdate:
    new_summary = summarize_service(state["text"])
    return {"summaries": [new_summary]}
```

The merged result is `["A", "B"]`. Return a delta because this field concatenates updates; a list field with replacement semantics may receive a complete new list.

中文上下文：先确定字段如何应用更新，再决定返回增量还是完整值，不把“仅返回新增项”推广到所有列表字段。
