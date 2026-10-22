from app import db


class Meta(db.Model):
    """Orçamento/meta mensal por categoria para um usuário."""
    __tablename__ = 'meta'

    id_meta = db.Column(db.Integer, primary_key=True)
    valor_limite = db.Column(db.Numeric(10, 2), nullable=False)
    mes = db.Column(db.Integer, nullable=False)
    ano = db.Column(db.Integer, nullable=False)

    # Chaves estrangeiras
    id_usuario = db.Column(db.Integer, db.ForeignKey('usuario.id_usuario'), nullable=False, index=True)
    id_categoria = db.Column(db.Integer, db.ForeignKey('categoria.id_categoria'), nullable=False)

    # Relacionamentos
    categoria = db.relationship('Categoria', backref='metas', lazy=True)

    # Constraint única: um usuário só pode ter uma meta por categoria/mês/ano
    __table_args__ = (
        db.UniqueConstraint('id_usuario', 'id_categoria', 'mes', 'ano', name='uq_meta_usuario_cat_periodo'),
    )

    def __repr__(self):
        return f'<Meta R${self.valor_limite} cat={self.id_categoria} {self.mes}/{self.ano}>'
