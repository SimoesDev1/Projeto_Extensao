from app import db
from werkzeug.security import generate_password_hash, check_password_hash


class Usuario(db.Model):
    __tablename__ = 'usuario'

    id_usuario = db.Column(db.Integer, primary_key=True)
    nome_usuario = db.Column(db.String(100), nullable=False, unique=True)
    senha = db.Column(db.String(255), nullable=False)

    # Relacionamento: um usuário realiza zero ou muitos gastos
    gastos = db.relationship('Gasto', backref='usuario', lazy=True)

    def set_senha(self, senha: str):
        self.senha = generate_password_hash(senha)

    def verificar_senha(self, senha: str) -> bool:
        return check_password_hash(self.senha, senha)

    def get_id(self):
        return str(self.id_usuario)

    def __repr__(self):
        return f'<Usuario {self.nome_usuario}>'
