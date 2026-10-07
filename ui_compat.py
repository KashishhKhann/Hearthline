"""Streamlit compatibility helpers.

`st.components.v1.html` is deprecated in newer Streamlit releases in favour of
`st.iframe`. This shim uses `st.iframe` when available and falls back to the old
API on older versions, so the app works across the supported range.
"""
from __future__ import annotations

import streamlit as st


def embed_html(html: str, height: int = 0, scrolling: bool = False) -> None:
    iframe = getattr(st, "iframe", None)
    if iframe is not None:
        iframe(html, height=max(int(height), 1))
        return
    import streamlit.components.v1 as components  # pragma: no cover - old Streamlit

    components.html(html, height=height, scrolling=scrolling)
