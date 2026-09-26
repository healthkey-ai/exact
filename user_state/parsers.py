"""A JSON parser that answers 400 instead of falling over.

`rest_framework.parsers.JSONParser` calls `json.loads`, which recurses once
per level of nesting. Python's own recursion limit is reached at roughly
10 000 levels, which a request body of about 20 KB carries comfortably —
far under Django's 2.5 MB `DATA_UPLOAD_MAX_MEMORY_SIZE`. Unbounded, that is
an uncaught `RecursionError` and an HTTP 500, repeatable at the throttle's
300 requests a minute by anyone with a token.

A validator cannot close this: it runs on the object the parser already
built, so above the parser's ceiling the request never reaches it. An
earlier version of this app said in a comment that the parser therefore
"cannot" be fixed. It can, and this is the fix — the cost is one try/except,
and the claim cost a 500 that the depth cap was believed to have closed.
"""
from rest_framework.exceptions import ParseError
from rest_framework.parsers import JSONParser


class BoundedJSONParser(JSONParser):
    """`JSONParser`, with runaway nesting reported rather than raised.

    `RecursionError` inherits from `RuntimeError`, not from `ValueError`, so
    DRF's own handling of a malformed body does not catch it.
    """

    def parse(self, stream, media_type=None, parser_context=None):
        try:
            return super().parse(stream, media_type, parser_context)
        except RecursionError:
            raise ParseError('JSON is nested too deeply to read.') from None
