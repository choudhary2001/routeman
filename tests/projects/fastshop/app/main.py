from fastapi import FastAPI, WebSocket

from .routers import auth, items, users

app = FastAPI(title='Fast Shop')
app.include_router(auth.router)
app.include_router(items.router, prefix='/api/v1')
app.include_router(users.router, prefix='/api/v1')


@app.get('/health', tags=['meta'])
def health():
    return {'status': 'ok'}


@app.get('/internal/stats', include_in_schema=False)
def stats(window: int = 60):
    """Hidden from the OpenAPI schema."""
    return {'window': window}


@app.websocket('/ws/orders/{order_id}')
async def order_updates(websocket: WebSocket, order_id: int):
    """Live order status."""
    await websocket.accept()
