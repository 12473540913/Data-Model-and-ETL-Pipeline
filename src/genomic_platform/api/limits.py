from starlette.responses import JSONResponse


class RequestLimits:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope["method"] not in ("GET", "POST", "HEAD", "OPTIONS"):
            await JSONResponse(
                {"error": {"code": "READ_ONLY", "message": "API is read-only."}}, status_code=405
            )(scope, receive, send)
            return
        if scope["method"] == "POST":
            chunks = []
            total = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                total += len(message.get("body", b""))
                if total > 65536:
                    await JSONResponse(
                        {
                            "error": {
                                "code": "QUERY_LIMIT",
                                "message": "Request body exceeds 65536 bytes.",
                            }
                        },
                        status_code=413,
                    )(scope, receive, send)
                    return
                chunks.append(message.get("body", b""))
                if not message.get("more_body"):
                    break
            messages = iter(
                [{"type": "http.request", "body": b"".join(chunks), "more_body": False}]
            )

            async def replay():
                return next(messages, {"type": "http.disconnect"})

            await self.app(scope, replay, send)
            return
        await self.app(scope, receive, send)
