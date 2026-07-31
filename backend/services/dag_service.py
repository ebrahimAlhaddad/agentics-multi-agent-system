"""Topology for run task graphs.

Deliberately pure: no database, no model calls, no framework objects. It takes
plan nodes (plain mappings, as the planner emits them) or task rows (anything
exposing task_id / depends_on / status) and returns decisions.

That purity is the point. Topology is where the subtle bugs live — a frontier
that stalls, a re-plan that orphans a node, a plan whose ordering is only
accidentally correct
"""

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Protocol, Sequence

import networkx as nx

from models.plan import Severity
from models.run import TaskStatus


class TaskLike(Protocol):
    """Structural type for a persisted task. RunTask satisfies it."""

    task_id: str
    depends_on: Sequence[str]
    status: str


@dataclass(frozen=True)
class PlanError:
    code: str
    detail: str
    node_id: Optional[str] = None
    severity: str = Severity.ERROR

    @property
    def blocking(self) -> bool:
        return self.severity == Severity.ERROR


class DagService:
    """Graph reasoning over a run's tasks."""

    # ------------------------------------------------------------------ build

    def build(self, nodes: Sequence[Mapping[str, Any]]) -> nx.DiGraph:
        """Plan nodes -> DiGraph, with edges pointing dependency -> dependent.

        Unknown dependency ids are added as nodes so that a malformed plan can
        still be inspected; validate() is what rejects them.
        """
        graph = nx.DiGraph()
        for node in nodes:
            graph.add_node(node["id"], **dict(node))
        for node in nodes:
            for dep in node.get("depends_on") or []:
                graph.add_edge(dep, node["id"])
        return graph

    # --------------------------------------------------------------- validate

    def validate(
        self,
        nodes: Sequence[Mapping[str, Any]],
        known_roles: Iterable[str],
        available: Iterable[str] = (),
        include_warnings: bool = False,
    ) -> list[PlanError]:
        """Structural checks on a plan. Empty list means it is safe to execute."""
        errors: list[PlanError] = []
        roles = set(known_roles)
        # Artifacts that already exist from earlier work. Only a re-plan passes
        # these: its nodes legitimately consume what completed tasks produced,
        # and those producers are not in the node list being validated.
        existing = set(available)

        if not nodes:
            return [PlanError("empty_plan", "plan contains no nodes")]

        # 1. duplicate ids — later checks assume ids are unique
        seen: set[str] = set()
        for node in nodes:
            node_id = node["id"]
            if node_id in seen:
                errors.append(
                    PlanError(
                        "duplicate_id",
                        f"node id {node_id!r} appears more than once",
                        node_id,
                    )
                )
            seen.add(node_id)

        # 2. dangling dependencies — models invent ids that were never emitted
        for node in nodes:
            for dep in node.get("depends_on") or []:
                if dep not in seen:
                    errors.append(
                        PlanError(
                            "dangling_dependency",
                            f"{node['id']!r} depends on unknown node {dep!r}",
                            node["id"],
                        )
                    )

        # 3. unknown roles — the orchestrator would have nothing to dispatch to
        for node in nodes:
            role = node.get("role")
            if role not in roles:
                errors.append(
                    PlanError(
                        "unknown_role",
                        f"{node['id']!r} has role {role!r}, expected one of {sorted(roles)}",
                        node["id"],
                    )
                )

        graph = self.build(nodes)

        # 4. cycles — a cyclic plan is a deadlock, and makes ancestry meaningless
        if not nx.is_directed_acyclic_graph(graph):
            try:
                cycle = nx.find_cycle(graph)
                detail = " -> ".join(str(a) for a, _ in cycle)
            except nx.NetworkXNoCycle:  # pragma: no cover - guarded by the check above
                detail = "unknown"
            errors.append(PlanError("cycle", f"plan has a dependency cycle: {detail}"))
            # Ancestry below is only meaningful on a DAG.
            return errors

        # 5. inputs must be produced by an ANCESTOR, not merely by some node.
        #    A producer that is not upstream is a race, not a dependency
        for node in nodes:
            consumes = node.get("consumes") or []
            if not consumes:
                continue
            upstream = nx.ancestors(graph, node["id"])
            reachable: set[str] = set(existing)
            for ancestor in upstream:
                reachable.update(graph.nodes[ancestor].get("produces") or [])
            for name in consumes:
                if name not in reachable:
                    errors.append(
                        PlanError(
                            "unproduced_input",
                            f"{node['id']!r} consumes {name!r}, which no upstream node produces",
                            node["id"],
                        )
                    )

        # 6. a task that reads nothing. Structurally the plan is consistent, but
        #    every role here works from data, so a task with no inputs has
        #    nothing to work from and would fail
        for node in nodes:
            if not (node.get("consumes") or []):
                errors.append(
                    PlanError(
                        "no_input",
                        f"{node['id']!r} consumes nothing — name the artifact it "
                        f"reads, including the run's input data",
                        node["id"],
                    )
                )

        # 7. orphans — legal, but usually a planner mistake worth surfacing
        if len(nodes) > 1:
            for node_id in nx.isolates(graph):
                errors.append(
                    PlanError(
                        "orphan_node",
                        f"{node_id!r} has no dependencies and nothing depends on it",
                        node_id,
                        severity=Severity.WARNING,
                    )
                )

        return errors if include_warnings else [e for e in errors if e.blocking]

    # --------------------------------------------------------------- runtime

    def frontier(self, tasks: Sequence[TaskLike]) -> list[TaskLike]:
        """Tasks that are dispatchable right now.

        Dispatchable means: not already done, failed or in flight, and every
        dependency is done. A dependency that failed does not satisfy anything,
        so its dependents stay blocked rather than running on missing inputs.
        """
        status_by_id = {t.task_id: t.status for t in tasks}
        ready = []
        for task in tasks:
            if task.status not in TaskStatus.DISPATCHABLE:
                continue
            deps = task.depends_on or []
            if all(status_by_id.get(d) == TaskStatus.DONE for d in deps):
                ready.append(task)
        return ready

    def invalidated_by(self, task_id: str, tasks: Sequence[TaskLike]) -> list[str]:
        """Ids of every task downstream of a failure.

        A task that fails in a way that invalidates the plan takes its whole
        descendant set with it — those tasks consume something that will never
        exist. The orchestrator hands this set to the planner to rewrite.
        """
        graph = self.build(
            [{"id": t.task_id, "depends_on": list(t.depends_on or [])} for t in tasks]
        )
        if task_id not in graph:
            return []
        return sorted(nx.descendants(graph, task_id))

    def is_drained(self, tasks: Sequence[TaskLike]) -> bool:
        """Every task has reached a terminal state."""
        return bool(tasks) and all(t.status in TaskStatus.TERMINAL for t in tasks)
dag_service = DagService()
