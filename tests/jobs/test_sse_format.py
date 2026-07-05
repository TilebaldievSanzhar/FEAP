"""Verify the SSE frame helper produces the expected wire format."""

from feap.api.jobs_router import _format_sse


def test_format_sse_with_dict():
    frame = _format_sse("item_done", {"item_id": "abc", "status": "completed"})
    text = frame.decode("utf-8")
    assert text.startswith("event: item_done\n")
    assert "\"item_id\": \"abc\"" in text
    assert text.endswith("\n\n")


def test_format_sse_with_string_data():
    frame = _format_sse("ping", "tick")
    assert frame == b"event: ping\ndata: tick\n\n"


def test_format_sse_preserves_unicode():
    frame = _format_sse("note", {"text": "мощность"})
    assert "мощность" in frame.decode("utf-8")
