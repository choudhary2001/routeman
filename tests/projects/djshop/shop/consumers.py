from channels.generic.websocket import JsonWebsocketConsumer


class StockConsumer(JsonWebsocketConsumer):
    """Live stock level of one product."""

    def connect(self):
        self.accept()
