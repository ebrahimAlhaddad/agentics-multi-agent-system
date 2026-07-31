"""Unit tests for the profiler. Pure — pandas in, dict out."""

import json

import pandas as pd
import pytest

from services.profile_service import MAX_CATEGORICAL_CARDINALITY, profile_service


def col(profile, name):
    return next(c for c in profile["columns"] if c["name"] == name)


@pytest.fixture
def sample():
    return pd.DataFrame({
        "customer_id": ["c1", "c2", "c3", "c4"],
        "mrr": [10.0, 20.0, 30.0, None],
        "plan": ["pro", "free", "pro", "pro"],
        "signed_up": pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01"]),
        "churn_reason": [None, None, None, None],
        "seats": [1, 2, 3, 4],
    })


# -------------------------------------------------------------------- shape


def test_reports_row_and_column_counts(sample):
    p = profile_service.profile(sample)
    assert p["row_count"] == 4
    assert p["column_count"] == 6
    assert len(p["columns"]) == 6


def test_every_column_has_the_core_fields(sample):
    for c in profile_service.profile(sample)["columns"]:
        assert {"name", "dtype", "nulls", "distinct"} <= set(c)


# -------------------------------------------------------------------- dtypes


@pytest.mark.parametrize("name,dtype", [
    ("customer_id", "string"), ("mrr", "float"), ("plan", "string"),
    ("signed_up", "datetime"), ("seats", "int"),
])
def test_dtypes_are_named_plainly(sample, name, dtype):
    assert col(profile_service.profile(sample), name)["dtype"] == dtype


def test_bool_is_not_reported_as_int():
    p = profile_service.profile(pd.DataFrame({"active": [True, False, True]}))
    assert col(p, "active")["dtype"] == "bool"


# --------------------------------------------------------------- statistics


def test_numeric_columns_get_min_max_mean(sample):
    mrr = col(profile_service.profile(sample), "mrr")
    assert mrr["min"] == 10.0 and mrr["max"] == 30.0 and mrr["mean"] == 20.0


def test_numeric_stats_are_rounded():
    p = profile_service.profile(pd.DataFrame({"x": [1 / 3, 2 / 3]}))
    assert col(p, "x")["mean"] == 0.5
    assert len(str(col(p, "x")["min"]).split(".")[-1]) <= 4


def test_datetime_columns_get_a_range(sample):
    signed = col(profile_service.profile(sample), "signed_up")
    assert "2024-01-01" in signed["min"] and "2024-04-01" in signed["max"]


def test_low_cardinality_strings_are_enumerated(sample):
    plan = col(profile_service.profile(sample), "plan")
    assert plan["top_values"][0] == {"value": "pro", "count": 3}


def test_top_values_are_capped():
    df = pd.DataFrame({"c": list("abcdefgh")})
    assert len(col(profile_service.profile(df, top_k=3), "c")["top_values"]) == 3


def test_high_cardinality_columns_are_described_not_enumerated():
    """A million distinct ids must not be listed into a prompt."""
    df = pd.DataFrame({"id": [f"id_{i}" for i in range(MAX_CATEGORICAL_CARDINALITY + 50)]})
    c = col(profile_service.profile(df), "id")
    assert "top_values" not in c
    assert len(c["examples"]) == 2


# -------------------------------------------------------------------- nulls


def test_null_rate_is_a_fraction(sample):
    assert col(profile_service.profile(sample), "mrr")["nulls"] == 0.25


def test_fully_null_column_is_flagged_and_not_analysed(sample):
    """The condition that should trigger a re-plan rather than a late failure."""
    c = col(profile_service.profile(sample), "churn_reason")
    assert c["all_null"] is True
    assert c["nulls"] == 1.0
    assert "top_values" not in c and "min" not in c


def test_column_with_no_nulls_has_a_zero_rate(sample):
    assert col(profile_service.profile(sample), "plan")["nulls"] == 0.0


# ------------------------------------------------------------ candidate keys


def test_unique_complete_column_is_a_candidate_key(sample):
    assert col(profile_service.profile(sample), "customer_id").get("candidate_key") is True


def test_repeating_column_is_not_a_candidate_key(sample):
    assert "candidate_key" not in col(profile_service.profile(sample), "plan")


def test_unique_but_incomplete_column_is_not_a_candidate_key():
    df = pd.DataFrame({"maybe": ["a", "b", None]})
    assert "candidate_key" not in col(profile_service.profile(df), "maybe")


# ------------------------------------------------------------- edge cases


def test_empty_dataframe_profiles_without_error():
    p = profile_service.profile(pd.DataFrame())
    assert p == {"row_count": 0, "column_count": 0, "columns": []}


def test_dataframe_with_columns_but_no_rows():
    p = profile_service.profile(pd.DataFrame({"a": pd.Series(dtype="float64")}))
    assert p["row_count"] == 0
    assert col(p, "a")["distinct"] == 0


def test_profile_is_json_serialisable(sample):
    """It is stored as JSONB and embedded in prompts — numpy types would break both."""
    json.dumps(profile_service.profile(sample))


def test_profile_size_does_not_grow_with_row_count():
    """The property that makes context O(schema) rather than O(data)."""
    small = pd.DataFrame({"a": range(10), "b": ["x"] * 10})
    large = pd.DataFrame({"a": range(100_000), "b": ["x"] * 100_000})
    assert len(json.dumps(profile_service.profile(large))) < \
        len(json.dumps(profile_service.profile(small))) * 2


def test_wide_dataset_profile_stays_modest():
    df = pd.DataFrame({f"c{i}": [1.0, 2.0] for i in range(200)})
    assert len(json.dumps(profile_service.profile(df))) < 40_000
