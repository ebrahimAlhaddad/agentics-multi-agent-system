"""Uploads against real Postgres + storage.

An upload is an artifact nobody produced, so these exercise the same service the
workers write through — that shared path is the point of the merge.
"""

import io

import pandas as pd
import pytest

from models.artifact import ArtifactKind, ArtifactOrigin
from services.artifact_service import artifact_service
from exceptions.exceptions import ServiceLayerException

CSV = (
    b"customer_id,mrr,plan\n"
    b"c1,10.0,pro\n"
    b"c2,20.0,free\n"
    b"c3,,pro\n"
)


@pytest.mark.asyncio
async def test_upload_stores_bytes_and_profile(postgres, storage, session_id):
    art = await artifact_service.process_upload(session_id, "subs.csv", CSV)

    # Named like any other artifact, qualified by a producer that is not a task.
    assert art.name == "input/subs"
    assert art.origin == ArtifactOrigin.INPUT
    assert art.kind == ArtifactKind.FRAME
    assert art.run_id is None and art.task_id is None

    profile = await artifact_service.profile(art.artifact_id)
    assert profile.row_count == 3
    assert {c["name"] for c in profile.columns} == {"customer_id", "mrr", "plan"}

    # Bytes in the object store, profile in the database. Stored as parquet, so
    # an input needs no special case anywhere downstream.
    assert (await storage.get(art.object_key))[:4] == b"PAR1"


@pytest.mark.asyncio
async def test_uploaded_frame_can_be_read_back(postgres, storage, session_id):
    art = await artifact_service.process_upload(session_id, "subs.csv", CSV)
    frame = await artifact_service.read(artifact_id=art.artifact_id)
    pd.testing.assert_frame_equal(
        frame.reset_index(drop=True),
        pd.read_csv(io.BytesIO(CSV)).reset_index(drop=True),
    )


@pytest.mark.asyncio
async def test_profile_flags_a_candidate_key(postgres, storage, session_id):
    art = await artifact_service.process_upload(session_id, "subs.csv", CSV)
    profile = await artifact_service.profile(art.artifact_id)
    by_name = {c["name"]: c for c in profile.columns}
    assert by_name["customer_id"].get("candidate_key") is True
    assert by_name["mrr"]["nulls"] > 0


@pytest.mark.asyncio
async def test_empty_upload_is_rejected(postgres, storage, session_id):
    with pytest.raises(ServiceLayerException):
        await artifact_service.process_upload(session_id, "empty.csv", b"")


@pytest.mark.asyncio
async def test_unparseable_upload_is_rejected_with_a_useful_message(
    postgres, storage, session_id
):
    with pytest.raises(ServiceLayerException) as e:
        await artifact_service.process_upload(
            session_id, "junk.csv", b"\xff\xfe\x00binary"
        )
    assert "CSV" in str(e.value)


@pytest.mark.asyncio
async def test_inputs_are_listed_per_session(postgres, storage, session_id):
    await artifact_service.process_upload(session_id, "a.csv", CSV)
    await artifact_service.process_upload(session_id, "b.csv", CSV)
    rows = await artifact_service.list_for(session_id=session_id, origin=ArtifactOrigin.INPUT)
    assert sorted(r.name for r in rows) == ["input/a", "input/b"]


@pytest.mark.asyncio
async def test_two_uploads_of_one_name_are_refused(postgres, storage, session_id):
    """The unique constraint doing its job, reported as a fixable mistake."""
    await artifact_service.process_upload(session_id, "subs.csv", CSV)
    with pytest.raises(ServiceLayerException) as e:
        await artifact_service.process_upload(session_id, "subs.csv", CSV)
    assert "already has an input" in str(e.value)
