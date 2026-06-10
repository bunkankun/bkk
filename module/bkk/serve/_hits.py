"""Shared helpers for turning an :class:`Index` ``Hit`` into a response model."""

from __future__ import annotations

from bkk.index.ir import Hit

from .schemas import HitOut, VariantOverlayOut


def hit_recipe(textid: str, hit: Hit) -> dict:
    """One-pin recipe pinning the hit's master span; re-submittable to /recipes:fulfil."""
    return {
        "pins": [
            {
                "role": "hit",
                "textid": textid,
                "selection": {
                    "juan": hit.juan_seq,
                    "bucket": hit.bucket,
                    "offset": hit.master_offset,
                    "length": hit.master_length,
                },
            }
        ]
    }


def hit_out(textid: str, h: Hit) -> HitOut:
    """Build the :class:`HitOut` response model for a single ``Hit``."""
    return HitOut(
        textid=h.textid,
        juan_seq=h.juan_seq,
        bucket=h.bucket,
        master_offset=h.master_offset,
        master_length=h.master_length,
        matched_via=h.matched_via,
        matched_text=h.matched_text,
        left=h.left,
        match=h.match,
        right=h.right,
        witness_left=h.witness_left,
        witness_right=h.witness_right,
        witness_left_variant_offset=h.witness_left_variant_offset,
        witness_right_variant_end=h.witness_right_variant_end,
        overlays=[
            VariantOverlayOut(
                master_offset=o.master_offset,
                length=o.length,
                content=o.content,
                witness=o.witness,
                witness_form=o.witness_form,
            )
            for o in h.overlays
        ],
        toc_label=h.toc_label,
        voice=h.voice,
        voice_stack=list(h.voice_stack),
        recipe=hit_recipe(textid, h),
    )
