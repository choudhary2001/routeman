from flask import Blueprint, jsonify, request
from flask_jwt_extended import create_access_token, create_refresh_token, get_jwt_identity, jwt_required

bp = Blueprint('auth', __name__)
USERS = {'alice': 'wonderland'}


@bp.route('/login', methods=['POST'])
def login():
    """Exchange a username and password for tokens."""
    data = request.get_json() or {}
    username = data.get('username')
    password = data.get('password')
    if USERS.get(username) != password:
        return jsonify(error='bad credentials'), 401
    return jsonify(access_token=create_access_token(identity=username),
                   refresh_token=create_refresh_token(identity=username))


@bp.post('/refresh')
@jwt_required(refresh=True)
def refresh():
    return jsonify(access_token=create_access_token(identity=get_jwt_identity()))


@bp.get('/me')
@jwt_required()
def me():
    return jsonify(user=get_jwt_identity())
