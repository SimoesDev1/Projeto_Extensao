from app import db


class Categoria(db.Model):
    __tablename__ = 'categoria'

    id_categoria = db.Column(db.Integer, primary_key=True)
    nome_categoria = db.Column(db.String(100), nullable=False, unique=True)

    # Subcategorias: referência ao pai (nullable = categoria raiz)
    id_categoria_pai = db.Column(db.Integer, db.ForeignKey('categoria.id_categoria'), nullable=True)
    subcategorias = db.relationship('Categoria', backref=db.backref('pai', remote_side=[id_categoria]), lazy='dynamic')

    # Relacionamento: uma categoria pertence a zero ou muitos gastos
    gastos = db.relationship('Gasto', backref='categoria', lazy=True)

    @property
    def nome_completo(self):
        """Retorna 'Pai > Filho' se for subcategoria."""
        if self.pai:
            return f"{self.pai.nome_categoria} > {self.nome_categoria}"
        return self.nome_categoria

    def __repr__(self):
        return f'<Categoria {self.nome_categoria}>'
