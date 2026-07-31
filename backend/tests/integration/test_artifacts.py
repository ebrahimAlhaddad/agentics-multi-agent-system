"""Artifacts across both stores."""

import pandas as pd
import pytest

from models.artifact import ArtifactKind, ArtifactOrigin
from services.artifact_service import artifact_service
from services.run_service import run_service
from exceptions.exceptions import NotFoundException

FRAME = pd.DataFrame({"cohort": ["2024-01", "2024-02"], "v": [1.0, 2.0]})


@pytest.mark.asyncio
async def test_frame_lives_in_storage_while_postgres_holds_the_pointer(
    postgres, storage, session_id
):
    """The claim the whole storage design rests on, checked from both sides."""
    run = await run_service.create_run(session_id=session_id)
    art = await artifact_service.put(
        "cohorts", ArtifactKind.FRAME, FRAME,
        run_id=run.run_id, task_id="n_cohorts"
    )

    # The name is qualified by its producer, so two tasks cannot collide.
    assert art.name == "n_cohorts/cohorts"
    assert art.origin == ArtifactOrigin.TRANSIENT
    # Nested under the session, so deleting one removes every object it owns.
    assert art.object_key == f"{run.session_id}/{art.artifact_id}.parquet"

    # Row counts live on the profile now — absence there is meaningful.
    profile = await artifact_service.profile(art.artifact_id)
    assert profile.row_count == 2
    assert [c["name"] for c in profile.columns] == ["cohort", "v"]

    raw = await storage.get(art.object_key)
    assert raw[:4] == b"PAR1", "object is not real parquet"

    from sqlalchemy import text
    async with postgres.engine.begin() as conn:
        row = (await conn.execute(text(
            "SELECT object_key, session_id FROM artifacts "
            "WHERE run_id=:r AND name='n_cohorts/cohorts'"
        ), {"r": run.run_id})).first()
    assert row[0] == art.object_key and row[1] == run.session_id

    pd.testing.assert_frame_equal(
        await artifact_service.read(run_id=run.run_id, name="n_cohorts/cohorts"), FRAME
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,payload", [
    (ArtifactKind.CHART, {"type": "line", "x": "cohort"}),
    (ArtifactKind.REPORT, "revenue is flat — café ☕"),
])
async def test_other_kinds_round_trip(postgres, storage, session_id, kind, payload):
    run = await run_service.create_run(session_id=session_id)
    await artifact_service.put("out", kind, payload, run_id=run.run_id, task_id="t")
    assert await artifact_service.read(run_id=run.run_id, name="t/out") == payload


@pytest.mark.asyncio
async def test_a_chart_has_no_profile(postgres, storage, session_id):
    """Absence is the point: nothing has to pretend a chart has columns."""
    run = await run_service.create_run(session_id=session_id)
    art = await artifact_service.put(
        "chart", ArtifactKind.CHART, {"type": "line"},
        run_id=run.run_id, task_id="t"
    )
    assert await artifact_service.profile(art.artifact_id) is None


@pytest.mark.asyncio
async def test_two_tasks_may_use_the_same_local_name(postgres, storage, session_id):
    """The collision the qualified name exists to prevent."""
    run = await run_service.create_run(session_id=session_id)
    a = await artifact_service.put("out", ArtifactKind.FRAME, FRAME, run_id=run.run_id, task_id="n_a")
    b = await artifact_service.put("out", ArtifactKind.FRAME, FRAME, run_id=run.run_id, task_id="n_b")

    assert {a.name, b.name} == {"n_a/out", "n_b/out"}
    pd.testing.assert_frame_equal(
        await artifact_service.read(run_id=run.run_id, name="n_b/out"), FRAME
    )


@pytest.mark.asyncio
async def test_handle_resolves_to_the_artifact(postgres, storage, session_id):
    run = await run_service.create_run(session_id=session_id)
    await artifact_service.put("cohorts", ArtifactKind.FRAME, FRAME, run_id=run.run_id, task_id="t")
    handle = artifact_service.handle(run.run_id, "t/cohorts")
    pd.testing.assert_frame_equal(await artifact_service.read_handle(handle), FRAME)


@pytest.mark.asyncio
async def test_a_png_round_trips_as_bytes(postgres, storage, session_id):
    """A plot or a pickle is bytes with an extension, and nothing more."""
    run = await run_service.create_run(session_id=session_id)
    png = b"\x89PNG\r\n\x1a\n and then some pixels"
    art = await artifact_service.put(
        "trend", ArtifactKind.FILE, png, suffix=".png",
        run_id=run.run_id, task_id="n_plot"
    )

    # The extension is the only record of what it is, so it must survive.
    assert art.object_key.endswith(".png")
    assert await artifact_service.read(run_id=run.run_id, name="n_plot/trend") == png
    # Bytes have no profile to record.
    assert await artifact_service.profile(art.artifact_id) is None


@pytest.mark.asyncio
async def test_a_file_without_a_suffix_is_refused(postgres, storage, session_id):
    run = await run_service.create_run(session_id=session_id)
    with pytest.raises(ValueError):
        await artifact_service.put(
            "x", ArtifactKind.FILE, b"bytes",
            run_id=run.run_id, task_id="t"
        )


@pytest.mark.asyncio
async def test_rewriting_a_name_replaces_it(postgres, storage, session_id):
    """An agent correcting itself, or a retried task, is not a duplicate key."""
    run = await run_service.create_run(session_id=session_id)
    first = await artifact_service.put(
        "out", ArtifactKind.FRAME, FRAME,
        run_id=run.run_id, task_id="n"
    )
    bigger = pd.DataFrame({"cohort": ["a", "b", "c"], "v": [1.0, 2.0, 3.0]})
    second = await artifact_service.put(
        "out", ArtifactKind.FRAME, bigger,
        run_id=run.run_id, task_id="n"
    )

    # Same row, so anything already pointing at it still resolves.
    assert first.artifact_id == second.artifact_id
    assert len(await artifact_service.read(run_id=run.run_id, name="n/out")) == 3
    profile = await artifact_service.profile(second.artifact_id)
    assert profile.row_count == 3


@pytest.mark.asyncio
async def test_replacing_with_another_kind_drops_the_old_object(
    postgres, storage, session_id
):
    run = await run_service.create_run(session_id=session_id)
    first = await artifact_service.put(
        "out", ArtifactKind.FRAME, FRAME,
        run_id=run.run_id, task_id="n"
    )
    old_key = first.object_key
    await artifact_service.put(
        "out", ArtifactKind.REPORT, "words instead",
        run_id=run.run_id, task_id="n"
    )

    assert not await storage.exists(old_key), "old object was orphaned"
    assert await artifact_service.read(run_id=run.run_id, name="n/out") == "words instead"


@pytest.mark.asyncio
async def test_a_local_name_resolves_to_its_producer(postgres, storage, session_id):
    """A plan consumes `totals`; storage holds `n_mrr/totals`. The prefix is the
    task id the plan is assigning, so it cannot write it and must not have to."""
    run = await run_service.create_run(session_id=session_id)
    await artifact_service.put("totals", ArtifactKind.FRAME, FRAME, run_id=run.run_id, task_id="n_mrr")

    found = await artifact_service.resolve(run_id=run.run_id, name="totals")
    assert found.name == "n_mrr/totals"
    # The qualified name still works, and is what a stored row reports.
    assert (await artifact_service.resolve(run_id=run.run_id, name="n_mrr/totals")).name == "n_mrr/totals"


@pytest.mark.asyncio
async def test_an_ambiguous_local_name_says_who_produced_it(postgres, storage, session_id):
    run = await run_service.create_run(session_id=session_id)
    await artifact_service.put("out", ArtifactKind.FRAME, FRAME, run_id=run.run_id, task_id="n_a")
    await artifact_service.put("out", ArtifactKind.FRAME, FRAME, run_id=run.run_id, task_id="n_b")

    with pytest.raises(NotFoundException) as e:
        await artifact_service.resolve(run_id=run.run_id, name="out")
    assert "n_a" in str(e.value) and "n_b" in str(e.value)
