import os
from datetime import timedelta
from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

class Config:
    BASE_DIR = BASE_DIR
    UPLOAD_COMPROVANTES_DIR = os.path.join(BASE_DIR, 'uploads', 'comprovantes')

    SECRET_KEY = os.getenv('SECRET_KEY', 'chave-padrao-segura')
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')

    # Sessão permanente com expiração de 8 horas
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

    # Ajusta cookie de sessão para HTTPS em produção (Render ou FLASK_ENV=production)
    IS_PRODUCTION = (
        os.getenv('RENDER') is not None
        or os.getenv('FLASK_ENV') == 'production'
        or os.getenv('ENV') == 'production'
        or os.getenv('SESSION_COOKIE_SECURE', '').lower() in ('true', '1')
    )
    SESSION_COOKIE_SECURE = IS_PRODUCTION

    # CSRF via Flask-WTF
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600  # 1 hora

    # Prioriza DATABASE_URL (PostgreSQL no Render/externo); trata postgres:// -> postgresql://
    # Mantém fallback automático para SQLite local em desenvolvimento
    default_db = f'sqlite:///{os.path.join(BASE_DIR, "db", "doce_como_mel.db")}'
    raw_db_uri = os.getenv('DATABASE_URL', '').strip()
    if not raw_db_uri:
        db_uri = default_db
    elif raw_db_uri.startswith('postgres://'):
        # SQLAlchemy 1.4+ requer o prefixo 'postgresql://'
        db_uri = raw_db_uri.replace('postgres://', 'postgresql://', 1)
    elif raw_db_uri.startswith('sqlite:///') and not os.path.isabs(raw_db_uri.replace('sqlite:///', '')):
        rel_path = raw_db_uri.replace('sqlite:///', '')
        db_uri = f'sqlite:///{os.path.join(BASE_DIR, rel_path)}'
    else:
        db_uri = raw_db_uri
    SQLALCHEMY_DATABASE_URI = db_uri

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Configurações de API e CORS (suporte ao Capacitor / APK mantendo web intacto)
    API_URL = os.getenv('API_URL', '')
    CORS_ORIGINS = os.getenv('CORS_ORIGINS', '')

    # Ajusta as opções de conexão dinamicamente com base no banco utilizado
    if db_uri.startswith('sqlite'):
        SQLALCHEMY_ENGINE_OPTIONS = {
            'connect_args': {'timeout': 30},
            'pool_pre_ping': True,
        }
    else:
        # PostgreSQL em produção (Render, Supabase, Neon)
        SQLALCHEMY_ENGINE_OPTIONS = {
            'connect_args': {'connect_timeout': 15},
            'pool_pre_ping': True,
            'pool_recycle': 300,
        }
