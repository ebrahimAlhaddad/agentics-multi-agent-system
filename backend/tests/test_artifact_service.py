"""Unit tests for artifact_service encoding and handles.

These need no database and no storage — they cover the parts that decide what an
artifact *means*. The two-store write path is exercised separately against live
infrastructure.
"""

import json

import pandas as pd
import pytest

from models.artifact import ArtifactKind
from services.artifact_service import ArtifactService, artifact_service

svc = ArtifactService


# ------------------------------------------------------------------- handles


def test_handle_format():
    assert artifact_service.handle("run_8f2a", "cohorts") == "artifact://run_8f2a/cohorts"


def test_handle_round_trips():
    h = artifact_service.handle("run_8f2a", "cohorts")
    assert artifact_service.parse_handle(h) == ("run_8f2a", "cohorts")


@pytest.mark.parametrize("bad", [
    "", "cohorts", "http://run/cohorts", "artifact://run_only", "artifact:///name",
    None,
])
def test_parse_handle_rejects_junk(bad):
    with pytest.raises(ValueError):
        artifact_service.parse_handle(bad)


def test_handle_carries_no_data():
    """The invariant the whole context design rests on: handles are references."""
    h = artifact_service.handle("run_1", "big_frame")
    assert len(h) < 120
    assert "artifact://" in h


# ------------------------------------------------------------------ encoding


def test_frame_round_trips_through_parquet():
    df = pd.DataFrame({"customer_id": ["a", "b"], "mrr": [10.5, 20.0]})
    back = svc.decode(ArtifactKind.FRAME, svc._encode(ArtifactKind.FRAME, df))
    pd.testing.assert_frame_equal(back, df)


def test_empty_frame_round_trips():
    df = pd.DataFrame({"a": pd.Series(dtype="float64")})
    assert svc.decode(ArtifactKind.FRAME, svc._encode(ArtifactKind.FRAME, df)).empty


def test_chart_round_trips_through_json():
    spec = {"type": "line", "x": "month", "y": "mrr", "series": [1, 2, 3]}
    data = svc._encode(ArtifactKind.CHART, spec)
    assert svc.decode(ArtifactKind.CHART, data) == spec
    assert json.loads(data) == spec


def test_report_round_trips_through_utf8():
    text = "Revenue is flat because expansion MRR collapsed — 40% lower."
    data = svc._encode(ArtifactKind.REPORT, text)
    assert svc.decode(ArtifactKind.REPORT, data) == text


def test_report_handles_non_ascii():
    text = "café ☕ 数据"
    assert svc.decode(ArtifactKind.REPORT, svc._encode(ArtifactKind.REPORT, text)) == text


def test_bytes_round_trip_untouched():
    """A PNG or a pickle goes in and comes out identical."""
    blob = b"\x89PNG\r\n\x1a\n\x00\xff"
    assert svc.decode(ArtifactKind.FILE, svc._encode(ArtifactKind.FILE, blob)) == blob


# ---------------------------------------------------------------- validation


@pytest.mark.parametrize("kind,obj", [
    (ArtifactKind.FRAME, {"not": "a frame"}),
    (ArtifactKind.FRAME, "csv,text"),
    (ArtifactKind.CHART, "not a dict"),
    (ArtifactKind.CHART, pd.DataFrame({"a": [1]})),
    (ArtifactKind.REPORT, {"not": "text"}),
    (ArtifactKind.REPORT, 42),
    (ArtifactKind.FILE, "not bytes"),
])
def test_encode_rejects_mismatched_types(kind, obj):
    """A kind/type mismatch must fail at the boundary, not produce junk bytes."""
    with pytest.raises(ValueError):
        svc._encode(kind, obj)


def test_encode_rejects_unknown_kind():
    with pytest.raises(ValueError):
        svc._encode("hologram", "x")


def test_decode_rejects_unknown_kind():
    with pytest.raises(ValueError):
        svc.decode("hologram", b"x")


def test_every_kind_but_file_has_a_fixed_extension():
    """FILE is the exception on purpose: it keeps whatever it was written with,
    which is the only record of whether it is a PNG or a pickle."""
    assert set(ArtifactKind.EXTENSIONS) == ArtifactKind.ALL - {ArtifactKind.FILE}
