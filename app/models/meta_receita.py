from app import db


class MetaReceita(db.Model):
    """Meta mensal de faturamento por tipo de receita para um usuário."""
    __tablename__ = 'meta_receita'

    id_meta_receita = db.Column(db.Integer, primary_key=True)
    tipo_receita = db.Column(db.String(50), nullable=False)   # ex: 'Venda', 'Encomenda'
    valor_meta = db.Column(db.Numeric(10, 2), nullable=False)
    mes = db.Column(db.Integer, nullable=False)
    ano = db.Column(db.Integer, nullable=False)

    # Chave estrangeira
    id_usuario = db.Column(db.Integer, db.ForeignKey('usuario.id_usuario'), nullable=False, index=True)

    # Constraint única
    __table_args__ = (
        db.UniqueConstraint('id_usuario', 'tipo_receita', 'mes', 'ano', name='uq_meta_receita_usuario_tipo_periodo'),
    )

    def __repr__(self):
        return f'<MetaReceita {self.tipo_receita} R${self.valor_meta} {self.mes}/{self.ano}>'
