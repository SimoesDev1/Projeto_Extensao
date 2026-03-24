from app import db
from datetime import date


class Gasto(db.Model):
    __tablename__ = 'gasto'

    id_gasto = db.Column(db.Integer, primary_key=True)
    valor = db.Column(db.Float, nullable=False)
    data_gasto = db.Column(db.Date, nullable=False, default=date.today)
    forma_pagamento = db.Column(db.String(50), nullable=False)
    descricao = db.Column(db.String(255), nullable=True)

    # Chaves estrangeiras conforme o DER
    id_usuario = db.Column(db.Integer, db.ForeignKey('usuario.id_usuario'), nullable=False)
    id_categoria = db.Column(db.Integer, db.ForeignKey('categoria.id_categoria'), nullable=False)

    def __repr__(self):
        return f'<Gasto R${self.valor} em {self.data_gasto}>'
