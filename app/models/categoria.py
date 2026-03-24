from app import db


class Categoria(db.Model):
    __tablename__ = 'categoria'

    id_categoria = db.Column(db.Integer, primary_key=True)
    nome_categoria = db.Column(db.String(100), nullable=False, unique=True)

    # Relacionamento: uma categoria pertence a zero ou muitos gastos
    gastos = db.relationship('Gasto', backref='categoria', lazy=True)

    def __repr__(self):
        return f'<Categoria {self.nome_categoria}>'
