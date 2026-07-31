"""Dataframe -> dataset semantic profile.

Pure: pandas in, plain dict out. No database, no storage, no model.

The profile is the structured description everything downstream reasons over
"""

from typing import Any, Optional

import pandas as pd
from pandas.api import types as ptypes

#: Distinct values shown for a low-cardinality column. Enough for a planner to
#: recognise a segmentation key, small enough that a hundred columns still fit.
TOP_K = 5

#: Above this many distinct values a column is described, not enumerated.
MAX_CATEGORICAL_CARDINALITY = 25


class ProfileService:
    def profile(self, df: pd.DataFrame, top_k: int = TOP_K) -> dict[str, Any]:
        rows = int(len(df))
        return {
            "row_count": rows,
            "column_count": int(len(df.columns)),
            "columns": [self.column(df[c], rows, top_k) for c in df.columns],
        }

    def column(
        self, series: pd.Series, row_count: int, top_k: int = TOP_K
    ) -> dict[str, Any]:
        nulls = int(series.isna().sum())
        distinct = int(series.nunique(dropna=True))

        info: dict[str, Any] = {
            "name": str(series.name),
            "dtype": self._dtype(series),
            "nulls": self._rate(nulls, row_count),
            "distinct": distinct,
        }

        # A column with no repeats and no gaps can identify a row — the single
        # most useful thing a planner can know when deciding how to join.
        if row_count and nulls == 0 and distinct == row_count:
            info["candidate_key"] = True

        if nulls == row_count and row_count:
            # Entirely empty. Say so plainly rather than emitting statistics over
            # nothing — this is exactly the condition that should trigger a
            # re-plan rather than a task failing mysteriously later.
            info["all_null"] = True
            return info

        info.update(self._statistics(series, distinct, top_k))
        return info

    # ------------------------------------------------------------- internals

    @staticmethod
    def _dtype(series: pd.Series) -> str:
        if ptypes.is_bool_dtype(series):
            return "bool"
        if ptypes.is_integer_dtype(series):
            return "int"
        if ptypes.is_float_dtype(series):
            return "float"
        if ptypes.is_datetime64_any_dtype(series):
            return "datetime"
        if ptypes.is_string_dtype(series) or ptypes.is_object_dtype(series):
            return "string"
        return str(series.dtype)

    @staticmethod
    def _rate(count: int, total: int) -> float:
        return round(count / total, 4) if total else 0.0

    def _statistics(
        self, series: pd.Series, distinct: int, top_k: int
    ) -> dict[str, Any]:
        clean = series.dropna()
        if clean.empty:
            return {}

        if ptypes.is_numeric_dtype(series) and not ptypes.is_bool_dtype(series):
            return {
                "min": self._number(clean.min()),
                "max": self._number(clean.max()),
                "mean": self._number(clean.mean()),
            }

        if ptypes.is_datetime64_any_dtype(series):
            return {"min": str(clean.min()), "max": str(clean.max())}

        # Categorical-ish. Enumerate only when the set is small enough to be
        # useful; otherwise a couple of examples convey the shape without the
        # cost.
        if distinct <= MAX_CATEGORICAL_CARDINALITY:
            counts = clean.value_counts().head(top_k)
            return {
                "top_values": [
                    {"value": str(v), "count": int(n)} for v, n in counts.items()
                ]
            }
        return {"examples": [str(v) for v in clean.head(2).tolist()]}

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        # Rounded because a planner never needs 15 significant figures, and a
        # hundred of them would cost real tokens.
        return round(number, 4)


profile_service = ProfileService()
