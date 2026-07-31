"""Unit tests for dag_service.

No database, no fixtures, no model calls — every case below is a hand-built
graph, which is the whole reason topology was kept out of the orchestrator.
"""

from dataclasses import dataclass, field


from models.plan import Severity
from models.run import TaskStatus
from services.dag_service import dag_service


#: The roles a plan may name. Hand-written rather than imported from the worker
#: registry: these tests take no dependency on which workers happen to exist.
ROLES = ("analyst", "synthesizer")


def validate(nodes, **kwargs):
    kwargs.setdefault("known_roles", ROLES)
    kwargs.setdefault("available", ["input/subs"])
    return dag_service.validate(nodes, **kwargs)



@dataclass
class FakeTask:
    """Stand-in for RunTask — satisfies the TaskLike protocol structurally."""

    task_id: str
    depends_on: list = field(default_factory=list)
    status: str = TaskStatus.PENDING


def node(nid, deps=(), produces=(), consumes=("input/subs",), role="analyst"):
    return {
        "id": nid,
        "role": role,
        "description": f"do {nid}",
        "depends_on": list(deps),
        "produces": list(produces),
        "consumes": list(consumes),
    }


def codes(errors):
    return sorted(e.code for e in errors)


# --------------------------------------------------------------------- build


def test_build_orients_edges_dependency_to_dependent():
    g = dag_service.build([node("a"), node("b", deps=["a"])])
    assert g.has_edge("a", "b")
    assert not g.has_edge("b", "a")


def test_build_keeps_node_attributes():
    g = dag_service.build([node("a", produces=["mrr"])])
    assert g.nodes["a"]["produces"] == ["mrr"]


# ------------------------------------------------------------------ validate


def test_valid_linear_plan_has_no_errors():
    plan = [
        node("a", produces=["mrr"]),
        node("b", deps=["a"], consumes=["mrr"]),
    ]
    assert validate(plan) == []


def test_valid_diamond_plan_has_no_errors():
    plan = [
        node("root", produces=["cohorts"]),
        node("l", deps=["root"], consumes=["cohorts"], produces=["ret"]),
        node("r", deps=["root"], consumes=["cohorts"], produces=["rev"]),
        node("join", deps=["l", "r"], consumes=["ret", "rev"], role="synthesizer"),
    ]
    assert validate(plan) == []


def test_empty_plan_is_rejected():
    assert codes(validate([])) == ["empty_plan"]


def test_duplicate_ids_are_rejected():
    assert "duplicate_id" in codes(validate([node("a"), node("a")]))


def test_dangling_dependency_is_rejected():
    errors = validate([node("a", deps=["ghost"])])
    assert "dangling_dependency" in codes(errors)
    assert any("ghost" in e.detail for e in errors)


def test_unknown_role_is_rejected():
    errors = validate([node("a", role="wizard")])
    assert "unknown_role" in codes(errors)


def test_direct_cycle_is_rejected():
    plan = [node("a", deps=["b"]), node("b", deps=["a"])]
    assert "cycle" in codes(validate(plan))


def test_longer_cycle_is_rejected():
    plan = [node("a", deps=["c"]), node("b", deps=["a"]), node("c", deps=["b"])]
    assert "cycle" in codes(validate(plan))


def test_self_dependency_is_a_cycle():
    assert "cycle" in codes(validate([node("a", deps=["a"])]))


def test_cycle_short_circuits_ancestry_checks():
    """Ancestry is meaningless on a cyclic graph, so it must not be attempted."""
    plan = [node("a", deps=["b"], consumes=["x"]), node("b", deps=["a"])]
    assert codes(validate(plan)) == ["cycle"]


def test_consuming_something_nobody_produces_is_rejected():
    plan = [node("a"), node("b", deps=["a"], consumes=["ghost"])]
    assert "unproduced_input" in codes(validate(plan))


def test_consuming_from_a_non_ancestor_is_rejected():
    """The subtle one: a producer that is not upstream is a race.

    'b' and 'producer' have no ordering between them, so both land in the same
    frontier round and 'b' can run before its input exists.
    """
    plan = [
        node("root"),
        node("producer", deps=["root"], produces=["mrr"]),
        node("b", deps=["root"], consumes=["mrr"]),
    ]
    errors = validate(plan)
    assert "unproduced_input" in codes(errors)
    assert any(e.node_id == "b" for e in errors)


def test_consuming_from_a_transitive_ancestor_is_allowed():
    plan = [
        node("a", produces=["mrr"]),
        node("b", deps=["a"]),
        node("c", deps=["b"], consumes=["mrr"]),
    ]
    assert validate(plan) == []


def test_orphan_is_a_warning_not_an_error():
    """An orphan is worth telling someone about but does not stop a run, so it
    only comes back when warnings are asked for."""
    plan = [node("a", produces=["x"]), node("b", deps=["a"], consumes=["x"]), node("lonely")]

    assert validate(plan) == []

    warned = validate(plan, include_warnings=True)
    assert codes(warned) == ["orphan_node"]
    assert warned[0].severity == Severity.WARNING


def test_single_node_plan_is_not_an_orphan():
    assert validate([node("only")]) == []


# ------------------------------------------------------------------ frontier


def test_frontier_starts_with_dependency_free_tasks():
    tasks = [FakeTask("a"), FakeTask("b"), FakeTask("c", ["a"])]
    assert {t.task_id for t in dag_service.frontier(tasks)} == {"a", "b"}


def test_frontier_opens_as_dependencies_complete():
    tasks = [
        FakeTask("a", status=TaskStatus.DONE),
        FakeTask("b", ["a"]),
        FakeTask("c", ["a", "b"]),
    ]
    assert [t.task_id for t in dag_service.frontier(tasks)] == ["b"]


def test_frontier_excludes_in_flight_and_terminal_tasks():
    tasks = [
        FakeTask("running", status=TaskStatus.RUNNING),
        FakeTask("validating", status=TaskStatus.VALIDATING),
        FakeTask("done", status=TaskStatus.DONE),
        FakeTask("failed", status=TaskStatus.FAILED),
        FakeTask("pending"),
    ]
    assert [t.task_id for t in dag_service.frontier(tasks)] == ["pending"]


def test_rework_is_redispatched():
    tasks = [FakeTask("a", status=TaskStatus.DONE), FakeTask("b", ["a"], TaskStatus.REWORK)]
    assert [t.task_id for t in dag_service.frontier(tasks)] == ["b"]


def test_failed_dependency_does_not_unblock_dependents():
    """A failed input is not a satisfied input — dependents must stay blocked."""
    tasks = [FakeTask("a", status=TaskStatus.FAILED), FakeTask("b", ["a"])]
    assert dag_service.frontier(tasks) == []


def test_fan_in_waits_for_every_dependency():
    tasks = [
        FakeTask("a", status=TaskStatus.DONE),
        FakeTask("b", status=TaskStatus.RUNNING),
        FakeTask("join", ["a", "b"]),
    ]
    assert dag_service.frontier(tasks) == []


def test_frontier_of_empty_task_list_is_empty():
    assert dag_service.frontier([]) == []


# ------------------------------------------------------------ invalidated_by


def test_invalidated_by_returns_all_descendants():
    tasks = [
        FakeTask("a"), FakeTask("b", ["a"]), FakeTask("c", ["b"]), FakeTask("side"),
    ]
    assert dag_service.invalidated_by("a", tasks) == ["b", "c"]


def test_invalidated_by_excludes_the_failure_itself():
    tasks = [FakeTask("a"), FakeTask("b", ["a"])]
    assert "a" not in dag_service.invalidated_by("a", tasks)


def test_invalidated_by_leaf_is_empty():
    tasks = [FakeTask("a"), FakeTask("b", ["a"])]
    assert dag_service.invalidated_by("b", tasks) == []


def test_invalidated_by_unknown_task_is_empty():
    assert dag_service.invalidated_by("ghost", [FakeTask("a")]) == []


def test_invalidated_by_spans_a_diamond():
    tasks = [
        FakeTask("root"), FakeTask("l", ["root"]), FakeTask("r", ["root"]),
        FakeTask("join", ["l", "r"]),
    ]
    assert dag_service.invalidated_by("root", tasks) == ["join", "l", "r"]


# ------------------------------------------------------- drained and stuck


def test_is_drained_only_when_every_task_is_terminal():
    done = [FakeTask("a", status=TaskStatus.DONE), FakeTask("b", status=TaskStatus.FAILED)]
    assert dag_service.is_drained(done)
    assert not dag_service.is_drained(done + [FakeTask("c")])


def test_a_task_that_reads_nothing_is_rejected():
    """Every role here works from data, so a task with no inputs would fail the
    moment it looked — cheaper to say so before the run starts."""
    errors = validate([node("a", consumes=[])], available=["input/subs"])
    assert "no_input" in codes(errors)


def test_an_input_that_exists_may_be_consumed_without_a_producer():
    """Root tasks read the run's inputs, which no task in the plan produces."""
    plan = [node("a", consumes=["input/subs"])]
    assert validate(plan) == []
    assert "unproduced_input" in codes(validate(plan, available=[])), (
        "a name nothing produces and no input provides still fails"
    )
