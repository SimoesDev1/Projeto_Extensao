from app import db
from datetime import date


class Gasto(db.Model):
    __tablename__ = 'gasto'

    id_gasto = db.Column(db.Integer, primary_key=True)
    valor = db.Column(db.Numeric(10, 2), nullable=False)
    data_gasto = db.Column(db.Date, nullable=False, default=date.today, index=True)
    forma_pagamento = db.Column(db.String(50), nullable=False)
    descricao = db.Column(db.String(255), nullable=True)

    # Chaves estrangeiras conforme o DER
    id_usuario = db.Column(db.Integer, db.ForeignKey('usuario.id_usuario'), nullable=False, index=True)
    id_categoria = db.Column(db.Integer, db.ForeignKey('categoria.id_categoria'), nullable=False)

    # Controle de Parcelamento (compras parceladas no cartão ou fornecedores)
    parcela_atual = db.Column(db.Integer, nullable=True, default=1)
    total_parcelas = db.Column(db.Integer, nullable=True, default=1)
    id_parcelamento = db.Column(db.String(36), nullable=True, index=True)

    # Comprovante / Nota Fiscal anexada (armazenado em uploads/comprovantes/)
    comprovante = db.Column(db.String(255), nullable=True)

    # Despesa recorrente: se True, é relançada mensalmente
    is_recorrente = db.Column(db.Boolean, nullable=False, default=False)
    # Dia do mês para relançamento automático (ex: 5 = todo dia 5)
    dia_recorrencia = db.Column(db.Integer, nullable=True)

    # Pago / pendente (útil para controle de contas a pagar)
    is_pago = db.Column(db.Boolean, nullable=False, default=False)

    @property
    def is_parcelado(self) -> bool:
        return bool(self.total_parcelas and self.total_parcelas > 1)

    def __repr__(self):
        if self.is_parcelado:
            return f'<Gasto R${self.valor} em {self.data_gasto} ({self.parcela_atual}/{self.total_parcelas})>'
        return f'<Gasto R${self.valor} em {self.data_gasto}>'
