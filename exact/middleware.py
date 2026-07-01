from whitenoise.middleware import WhiteNoiseMiddleware


class CorsWhiteNoiseMiddleware(WhiteNoiseMiddleware):
    """WhiteNoise subclass that adds CORS headers to static responses.

    Module Federation remotes are loaded cross-origin by the host app.
    WhiteNoise serves files before Django middleware runs, so
    django-cors-headers can't help — the CORS header must be added here.
    """

    def __call__(self, request):
        response = super().__call__(request)
        is_static = getattr(response, "file_to_stream", None) or response.get(
            "X-Whitenoise", None
        )
        if is_static:
            response["Access-Control-Allow-Origin"] = "*"
        return response
