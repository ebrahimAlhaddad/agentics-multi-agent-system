"""Tools for looking at the data.

The SDK builds each schema from the function itself: the name from the function
name, the description from the first docstring line, argument descriptions from
the `Args:` section, and types from the annotations. So the function is the
whole definition — there is no registry, no adapter and no descriptor to keep in
step with it.

`RunContextWrapper` is the security boundary, and it must be the first argument.
Everything after it is the model's to choose; the context is not, and the SDK
never sends it to the model. So a caller may name an artifact_id but never a
session_id, and every handler resolves the artifact through the session on the
context — which means a prompt injection sitting in a CSV cannot reach another
user's data.
"""

import json
from uuid import UUID

from agents import RunContextWrapper, function_tool

from models.artifact import ArtifactOrigin
from models.run_context import RunContext
from services.artifact_service import artifact_service
from services.run_service import run_service

#: Ceilings on what one call may return. A tool result goes straight into the
#: model's context, so an unbounded one is an unbounded bill.
MAX_SAMPLE_ROWS = 50
MAX_VALUES = 50


@function_tool
async def list_inputs(wrapper: RunContextWrapper[RunContext]) -> str:
    """List the data available in this session."""
    rows = await artifact_service.list_for(session_id=wrapper.context.session_id, origin=ArtifactOrigin.INPUT)
    out = []
    for artifact in rows:
        profile = await artifact_service.profile(artifact.artifact_id)
        out.append(
            {
                "artifact_id": str(artifact.artifact_id),
                "name": artifact.name,
                "rows": profile.row_count if profile else None,
            }
        )
    return json.dumps(out)


@function_tool
async def describe_input(
    wrapper: RunContextWrapper[RunContext], artifact_id: str
) -> str:
    """Column names, types, null rates and distinct counts.

    Start here — it is cheap and says what exists.

    Args:
        artifact_id: Which input to describe.
    """
    artifact = await artifact_service.resolve(
        artifact_id=UUID(artifact_id), session_id=wrapper.context.session_id
    )
    profile = await artifact_service.profile(artifact.artifact_id)
    return json.dumps(
        {
            "name": artifact.name,
            "rows": profile.row_count if profile else None,
            "columns": (profile.columns if profile else []) or [],
        },
        default=str,
    )


@function_tool
async def sample_rows(
    wrapper: RunContextWrapper[RunContext], artifact_id: str, limit: int = 10
) -> str:
    """Real rows from an input.

    Use it to see how values are actually written before assuming a format or a
    category name.

    Args:
        artifact_id: Which input to read from.
        limit: How many rows to return, at most 50.
    """
    frame = await artifact_service.read(
        artifact_id=UUID(artifact_id), session_id=wrapper.context.session_id
    )
    head = frame.head(min(limit, MAX_SAMPLE_ROWS))
    return json.dumps(
        {
            "columns": [str(c) for c in head.columns],
            "rows": head.to_dict(orient="records"),
            "total_rows": len(frame),
        },
        default=str,
    )


@function_tool
async def column_values(
    wrapper: RunContextWrapper[RunContext],
    artifact_id: str,
    column: str,
    limit: int = 20,
) -> str:
    """The most common values in one column, with counts.

    Use it to find the real categories in a column rather than guessing them.

    Args:
        artifact_id: Which input to read from.
        column: The column to count values in.
        limit: How many distinct values to return, at most 50.
    """
    frame = await artifact_service.read(
        artifact_id=UUID(artifact_id), session_id=wrapper.context.session_id
    )
    if column not in frame.columns:
        # An error the model can act on beats an empty result it will misread
        # as "the column is empty".
        return json.dumps(
            {
                "error": f"no such column: {column}",
                "available": [str(c) for c in frame.columns],
            }
        )

    counts = frame[column].value_counts(dropna=False).head(min(limit, MAX_VALUES))
    return json.dumps(
        {
            "column": column,
            "distinct": int(frame[column].nunique(dropna=True)),
            "values": [{"value": v, "count": int(n)} for v, n in counts.items()],
        },
        default=str,
    )


@function_tool
async def use_inputs(
    wrapper: RunContextWrapper[RunContext], artifact_ids: list[str]
) -> str:
    """Record which inputs this analysis works with.

    Call it once you know which ones you need — a run may use more than one, and
    calling it again replaces the list rather than adding to it.

    Args:
        artifact_ids: The inputs to use, by id from list_inputs.
    """
    ctx = wrapper.context
    chosen = []
    for artifact_id in artifact_ids:
        # Scoped, so naming an artifact from another session reads as absent.
        artifact = await artifact_service.resolve(
            artifact_id=UUID(artifact_id), session_id=ctx.session_id
        )
        chosen.append(artifact.name)

    run = await run_service.get_run(ctx.run_id)
    await run_service.set_inputs(run, artifact_ids)
    return f"Using {', '.join(chosen)}."


DATA_TOOLS = [list_inputs, describe_input, sample_rows, column_values, use_inputs]
