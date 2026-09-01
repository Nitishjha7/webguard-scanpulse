"""Extension singletons, instantiated here to avoid circular imports.

Each is bound to the Flask app inside ``create_app()``.
"""
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
migrate = Migrate()
jwt = JWTManager()
cors = CORS()
