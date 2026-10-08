from flask import Flask
from flask_jwt_extended import JWTManager


def create_app():
    app = Flask(__name__)
    app.config['JWT_SECRET_KEY'] = 'test-secret-key-that-is-long-enough-32b'
    JWTManager(app)
    from .auth import bp as auth_bp
    from .books import bp as books_bp, register_views
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(books_bp, url_prefix='/api/books')
    register_views(app)

    @app.get('/ping')
    def ping():
        """Liveness probe."""
        return {'pong': True}

    return app
