from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from config import Config

db = SQLAlchemy()


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)

    # Importa os models para que o SQLAlchemy os registre antes do create_all
    from app.models import Usuario, Categoria, Gasto

    # Registra os blueprints (rotas)
    from app.routes.auth import auth_bp
    from app.routes.gastos import gastos_bp
    from app.routes.dashboard import dashboard_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(gastos_bp)
    app.register_blueprint(dashboard_bp)

    with app.app_context():
        db.create_all()

    return app
