import os
from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'chave-padrao-segura')
    
    # Prioriza DATABASE_URL do .env, caso contrário usa o SQLite padrão
    default_db = f'sqlite:///{os.path.join(BASE_DIR, "db", "doce_como_mel.db")}'
    db_uri = os.getenv('DATABASE_URL', default_db)
    SQLALCHEMY_DATABASE_URI = db_uri
    
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Ajusta as opções de conexão dinamicamente com base no banco utilizado
    if db_uri.startswith('sqlite'):
        SQLALCHEMY_ENGINE_OPTIONS = {
            'connect_args': {'timeout': 30},
            'pool_pre_ping': True,
        }
    else:
        SQLALCHEMY_ENGINE_OPTIONS = {
            'connect_args': {'connect_timeout': 30},
            'pool_pre_ping': True,
        }
