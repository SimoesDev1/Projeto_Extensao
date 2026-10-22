from app import db
from datetime import date

class Receita(db.Model):
    __tablename__ = 'receita'

    id_receita = db.Column(db.Integer, primary_key=True)
    valor = db.Column(db.Numeric(10, 2), nullable=False)
    data_receita = db.Column(db.Date, nullable=False, default=date.today, index=True)
    forma_recebimento = db.Column(db.String(50), nullable=False)
    tipo_receita = db.Column(db.String(50), nullable=True, default='Venda')
    descricao = db.Column(db.String(255), nullable=True)

    # Comprovante / Nota Fiscal anexada (armazenado em uploads/comprovantes/)
    comprovante = db.Column(db.String(255), nullable=True)

    # Relacionamento com Usuário
    id_usuario = db.Column(db.Integer, db.ForeignKey('usuario.id_usuario'), nullable=False, index=True)

    def __repr__(self):
        return f'<Receita R${self.valor} em {self.data_receita}>'
