from app import db
# responsável por criptografar as senhas dos usuários
from werkzeug.security import generate_password_hash, check_password_hash

PERFIL_ADMIN = 'admin'
PERFIL_OPERADOR = 'operador'


class Usuario(db.Model):
    __tablename__ = 'usuario'

    id_usuario = db.Column(db.Integer, primary_key=True)
    nome_usuario = db.Column(db.String(100), nullable=False, unique=True)
    senha = db.Column(db.String(255), nullable=False)

    # Multi-usuário: perfil de acesso
    perfil = db.Column(db.String(20), nullable=False, default=PERFIL_ADMIN)
    # ID do usuário "dono" da conta (para operadores vinculados a um admin)
    id_usuario_pai = db.Column(db.Integer, db.ForeignKey('usuario.id_usuario'), nullable=True)
    subordinados = db.relationship('Usuario', backref=db.backref('pai', remote_side=[id_usuario]), lazy='dynamic')

    # Relacionamentos
    gastos = db.relationship('Gasto', backref='usuario', lazy=True)
    receitas = db.relationship('Receita', backref='usuario', lazy=True)
    metas = db.relationship('Meta', backref='usuario', lazy=True)

    def set_senha(self, senha: str):
        self.senha = generate_password_hash(senha)

    def verificar_senha(self, senha: str) -> bool:
        return check_password_hash(self.senha, senha)

    @property
    def is_admin(self) -> bool:
        return self.perfil == PERFIL_ADMIN

    @property
    def is_operador(self) -> bool:
        return self.perfil == PERFIL_OPERADOR

    def get_id(self):
        return str(self.id_usuario)

    def __repr__(self):
        return f'<Usuario {self.nome_usuario} [{self.perfil}]>'
