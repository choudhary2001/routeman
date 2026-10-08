from flask import Blueprint, jsonify, request
from flask.views import MethodView
from flask_jwt_extended import jwt_required
from marshmallow import Schema, fields, validate

bp = Blueprint('books', __name__)
BOOKS = {1: {'id': 1, 'title': 'Dune', 'price': 9.5, 'genre': 'scifi'}}


class BookSchema(Schema):
    title = fields.String(required=True, validate=validate.Length(min=1, max=100))
    price = fields.Float(required=True, validate=validate.Range(min=0))
    genre = fields.String(load_default='fiction', validate=validate.OneOf(['fiction', 'scifi', 'history']))
    tags = fields.List(fields.String())
    published = fields.Date()


@bp.get('/')
def list_books():
    """List books, optionally filtered by genre."""
    genre = request.args.get('genre')
    page = request.args.get('page', 1, type=int)
    books = [b for b in BOOKS.values() if not genre or b['genre'] == genre]
    return jsonify(results=books, page=page)


@bp.post('/')
@jwt_required()
def create_book():
    data = BookSchema().load(request.json)
    book_id = max(BOOKS) + 1
    BOOKS[book_id] = {'id': book_id, **{k: v for k, v in data.items() if k != 'published'}}
    return jsonify(BOOKS[book_id]), 201


@bp.route('/<int:book_id>', methods=['GET', 'DELETE'])
@jwt_required()
def book_detail(book_id):
    book = BOOKS.get(book_id)
    if book is None:
        return jsonify(error='not found'), 404
    if request.method == 'DELETE':
        BOOKS.pop(book_id)
        return '', 204
    return jsonify(book)


@bp.post('/<int:book_id>/cover')
@jwt_required()
def upload_cover(book_id):
    cover = request.files['cover']
    caption = request.form.get('caption', '')
    return jsonify(book=book_id, filename=cover.filename, caption=caption)


class ReviewAPI(MethodView):
    decorators = [jwt_required()]

    def get(self, book_id):
        return jsonify(reviews=[])

    def post(self, book_id):
        """Add a review."""
        payload = request.get_json()
        rating = int(payload['rating'])
        return jsonify(book=book_id, rating=rating, text=payload.get('text', '')), 201


def register_views(app):
    app.add_url_rule('/api/books/<int:book_id>/reviews', view_func=ReviewAPI.as_view('reviews'))
