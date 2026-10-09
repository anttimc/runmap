"""Profile-filtered block-local embeddings for cycle optimization."""

from runmap.routing.profiles import classify_segments, effective_edge_profile
from runmap.routing.topology import (
    complete_block_embedding,
    connectivity_for_segments,
)


def build_cycle_block_topology(index, topology, edge_profile=None, stairs=None):
    """Build complete planar embeddings after profile filtering and block split."""
    effective_profile = effective_edge_profile(edge_profile, stairs)
    source_segment_ids = set(topology["face_embedding"]["segment_ids"])
    all_reasons = (
        classify_segments(index, effective_profile) if effective_profile else {}
    )
    forbidden_reasons = {
        segment_id: reason
        for segment_id, reason in all_reasons.items()
        if segment_id in source_segment_ids
    }
    allowed_segment_ids = source_segment_ids - forbidden_reasons.keys()
    connectivity = connectivity_for_segments(index, allowed_segment_ids)
    blocks = sorted(
        connectivity["cyclic_blocks"],
        key=lambda block: tuple(sorted(block["segment_ids"])),
    )
    embeddings = [
        complete_block_embedding(
            index,
            block["segment_ids"],
            block_id=f"cycle-block-{offset}",
        )
        for offset, block in enumerate(blocks, 1)
    ]
    return {
        "schema_version": 1,
        "effective_edge_profile": effective_profile,
        "source_segment_ids": sorted(source_segment_ids),
        "allowed_segment_ids": sorted(allowed_segment_ids),
        "forbidden_segment_reasons": forbidden_reasons,
        "blocks": embeddings,
        "summary": {
            "source_segment_count": len(source_segment_ids),
            "allowed_segment_count": len(allowed_segment_ids),
            "forbidden_segment_count": len(forbidden_reasons),
            "cyclic_block_count": len(embeddings),
            "valid_block_count": sum(
                block["valid_for_dual"] for block in embeddings
            ),
            "invalid_block_count": sum(
                not block["valid_for_dual"] for block in embeddings
            ),
            "selectable_face_count": sum(
                len(block["selectable_face_ids"]) for block in embeddings
            ),
        },
    }
