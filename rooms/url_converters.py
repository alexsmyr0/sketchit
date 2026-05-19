"""URL path converters for the rooms app.

Centralizes the join-code shape so every URL that accepts one rejects
garbage at routing time, before any view code or database query runs.
"""


class JoinCodeConverter:
    """Match a Sketchit join code.

    Codes are generated as 8 uppercase ASCII alphanumerics
    (``string.ascii_uppercase + string.digits``). The regex accepts
    either case so existing links like ``/rooms/abc12345/`` still work;
    ``to_python`` normalises to upper so views always see the canonical
    form. Anything that does not look like a join code (wrong length,
    punctuation, path traversal, etc.) misses the route entirely and
    Django returns 404 without entering the view.
    """

    regex = r"[A-Za-z0-9]{8}"

    def to_python(self, value: str) -> str:
        return value.upper()

    def to_url(self, value: str) -> str:
        return value
