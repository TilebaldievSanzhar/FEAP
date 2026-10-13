"""JWT из ?access_token= (SSE) не должен попадать в access-лог uvicorn."""

import logging

from feap.auth.access import RedactQueryTokenFilter


def _uvicorn_record(path: str) -> logging.LogRecord:
    # Форма записи uvicorn.access: '%s - "%s %s HTTP/%s" %d'
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 0,
        '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:1", "GET", path, "1.1", 200), None,
    )


def test_masks_token_in_uvicorn_args():
    rec = _uvicorn_record("/api/v1/jobs/j1/events?access_token=eyJ.abc.def")
    assert RedactQueryTokenFilter().filter(rec) is True
    line = rec.getMessage()
    assert "eyJ" not in line
    assert "/api/v1/jobs/j1/events?access_token=***" in line


def test_keeps_other_query_params():
    rec = _uvicorn_record("/x?since=5&access_token=t0k&limit=2")
    RedactQueryTokenFilter().filter(rec)
    assert rec.getMessage().endswith('/x?since=5&access_token=***&limit=2 HTTP/1.1" 200')


def test_untouched_without_token():
    rec = _uvicorn_record("/api/v1/jobs/j1")
    RedactQueryTokenFilter().filter(rec)
    assert "/api/v1/jobs/j1 HTTP/1.1" in rec.getMessage()


def test_plain_message_without_args():
    rec = logging.LogRecord("x", logging.INFO, __file__, 0, "GET /e?access_token=abc", None, None)
    RedactQueryTokenFilter().filter(rec)
    assert rec.getMessage() == "GET /e?access_token=***"
