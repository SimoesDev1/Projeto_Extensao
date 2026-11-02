import os
import logging
from flask import Flask, send_from_directory, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from flask_cors import CORS
from config import Config

db = SQLAlchemy()
csrf = CSRFProtect()
limiter = Limiter(key_func=get_remote_address, default_limits=[], storage_uri="memory://")
migrate = Migrate()
cors = CORS()

# Logging básico
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


def create_app():
    import os
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    migrate.init_app(app, db)

    # ── Suporte a Reverse Proxy / HTTPS em Produção (Render / Nuvem) ───────
    if app.config.get('SESSION_COOKIE_SECURE') or os.getenv('RENDER'):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    # ── Configuração de CORS (mantém origens existentes e adiciona Capacitor/localhost) ──
    cors_origins_env = app.config.get('CORS_ORIGINS') or os.getenv('CORS_ORIGINS', '')
    default_origins = [
        'http://localhost',
        'capacitor://localhost',
        'http://localhost:5000',
        'http://127.0.0.1:5000',
        'http://localhost:8100'
    ]
    if cors_origins_env == '*':
        cors.init_app(app, resources={r"/*": {"origins": "*"}})
    else:
        if cors_origins_env:
            extra_origins = [o.strip() for o in cors_origins_env.split(',') if o.strip()]
            allowed_origins = list(dict.fromkeys(default_origins + extra_origins))
        else:
            allowed_origins = default_origins

        cors.init_app(
            app,
            resources={r"/*": {"origins": allowed_origins}},
            supports_credentials=True,
            allow_headers=["Content-Type", "Authorization", "X-CSRFToken", "X-Requested-With"],
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
        )

    # Importa os models para que o SQLAlchemy os registre antes do create_all
    from app.models import Usuario, Categoria, Gasto, Receita
    from app.models.meta import Meta
    from app.models.meta_receita import MetaReceita

    # Registra os blueprints (rotas)
    from app.routes.auth import auth_bp
    from app.routes.gastos import gastos_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.receitas import receitas_bp
    from app.routes.categorias import categorias_bp
    from app.routes.metas import metas_bp
    from app.routes.relatorios import relatorios_bp
    from app.routes.ia_fiscal import ia_fiscal_bp
    from app.routes.reajuste_ipca import reajuste_ipca_bp
    # from app.routes.precificacao import precificacao_bp
    from app.routes.ia_chat import ia_chat_bp
    from app.routes.comprovantes import comprovantes_bp
    from app.routes.metas_receita import metas_receita_bp
    # from app.routes.usuarios import usuarios_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(gastos_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(receitas_bp)
    app.register_blueprint(categorias_bp)
    app.register_blueprint(metas_bp)
    app.register_blueprint(relatorios_bp)
    app.register_blueprint(ia_fiscal_bp)
    app.register_blueprint(reajuste_ipca_bp)
    # app.register_blueprint(precificacao_bp)
    app.register_blueprint(ia_chat_bp)
    app.register_blueprint(comprovantes_bp)
    app.register_blueprint(metas_receita_bp)
    # app.register_blueprint(usuarios_bp)

    with app.app_context():
        import os
        # Cria a pasta de comprovantes de forma robusta, sem caminhos absolutos fixos.
        _upload_dir = app.config.get('UPLOAD_COMPROVANTES_DIR') or os.path.join(
            app.config.get('BASE_DIR', os.path.dirname(os.path.dirname(__file__))),
            'uploads', 'comprovantes'
        )
        os.makedirs(_upload_dir, exist_ok=True)
        db.create_all()

        # ── Migrações suaves de colunas novas ──────────────────────────────────
        try:
            from sqlalchemy import inspect, text
            inspector = inspect(db.engine)

            # ── tabela receita ──
            cols_receita = [c['name'] for c in inspector.get_columns('receita')]
            _add_col_if_missing(db, cols_receita, 'receita', 'tipo_receita', "VARCHAR(50) DEFAULT 'Venda'")
            _add_col_if_missing(db, cols_receita, 'receita', 'comprovante', "VARCHAR(255)")

            # ── tabela gasto ──
            cols_gasto = [c['name'] for c in inspector.get_columns('gasto')]
            _add_col_if_missing(db, cols_gasto, 'gasto', 'parcela_atual', "INTEGER DEFAULT 1")
            _add_col_if_missing(db, cols_gasto, 'gasto', 'total_parcelas', "INTEGER DEFAULT 1")
            _add_col_if_missing(db, cols_gasto, 'gasto', 'id_parcelamento', "VARCHAR(36)")
            _add_col_if_missing(db, cols_gasto, 'gasto', 'comprovante', "VARCHAR(255)")
            _add_col_if_missing(db, cols_gasto, 'gasto', 'is_recorrente', "BOOLEAN NOT NULL DEFAULT 0")
            _add_col_if_missing(db, cols_gasto, 'gasto', 'dia_recorrencia', "INTEGER")
            _add_col_if_missing(db, cols_gasto, 'gasto', 'is_pago', "BOOLEAN NOT NULL DEFAULT 1")

            # ── tabela categoria ──
            cols_cat = [c['name'] for c in inspector.get_columns('categoria')]
            _add_col_if_missing(db, cols_cat, 'categoria', 'id_categoria_pai',
                                "INTEGER REFERENCES categoria(id_categoria)")

            # ── tabela usuario ──
            cols_usr = [c['name'] for c in inspector.get_columns('usuario')]
            _add_col_if_missing(db, cols_usr, 'usuario', 'perfil', "VARCHAR(20) NOT NULL DEFAULT 'admin'")
            _add_col_if_missing(db, cols_usr, 'usuario', 'id_usuario_pai',
                                "INTEGER REFERENCES usuario(id_usuario)")

        except Exception as e:
            logger.warning(f"Aviso ao verificar esquema: {e}")

    # ── Rota autenticada para servir comprovantes anexados ──
    @app.route('/comprovantes/<path:filename>')
    def servir_comprovante(filename):
        from flask import send_from_directory, abort, session
        if 'id_usuario' not in session:
            abort(401)
        safe_filename = os.path.basename(filename)
        return send_from_directory(app.config['UPLOAD_COMPROVANTES_DIR'], safe_filename)

    # ── PWA: manifest e service worker ──
    @app.route('/manifest.json')
    def pwa_manifest():
        from flask import send_from_directory
        return send_from_directory(app.static_folder, 'manifest.json')

    @app.route('/sw.js')
    def service_worker():
        from flask import send_from_directory
        return send_from_directory(app.static_folder, 'sw.js')

    @app.route('/favicon.ico')
    def favicon():
        import os
        from flask import send_from_directory
        return send_from_directory(os.path.join(app.static_folder, 'img'), 'favicon.ico')

    # ── Keep-alive / Health Check (Público, sem auth, sem rate limit) ─────────
    @app.route('/health', methods=['GET'])
    @limiter.exempt
    @csrf.exempt
    def health():
        try:
            from sqlalchemy import text
            db.session.execute(text('SELECT 1'))
            return jsonify({'status': 'ok'}), 200
        except Exception as e:
            logger.error(f"Health check falhou: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 503

    return app


def _add_col_if_missing(db, existing_cols: list, table: str, col: str, col_def: str):
    """Adiciona uma coluna à tabela se ela ainda não existir (migration suave)."""
    if col not in existing_cols:
        try:
            with db.engine.begin() as conn:
                conn.execute(__import__('sqlalchemy').text(
                    f"ALTER TABLE {table} ADD COLUMN {col} {col_def}"
                ))
            logger.info(f"Coluna '{col}' adicionada à tabela '{table}'.")
        except Exception as e:
            logger.warning(f"Não foi possível adicionar '{col}' em '{table}': {e}")
