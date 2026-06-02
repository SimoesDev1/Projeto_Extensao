from app import db
from app.models.gasto import Gasto
from sqlalchemy import func, extract


class GastoService:
    """Regras de negócio relacionadas ao Gasto."""

    @staticmethod
    def listar_por_usuario(id_usuario: int) -> list[Gasto]:
        return (Gasto.query
                .filter_by(id_usuario=id_usuario)
                .order_by(Gasto.data_gasto.desc())
                .all())

    @staticmethod
    def filtrar_por_periodo(id_usuario: int, mes: int, ano: int) -> list[Gasto]:
        """Filtra gastos por mês e ano usando extract (compatível com SQLite e PostgreSQL)."""
        return (Gasto.query
                .filter_by(id_usuario=id_usuario)
                .filter(extract('month', Gasto.data_gasto) == mes)
                .filter(extract('year', Gasto.data_gasto) == ano)
                .order_by(Gasto.data_gasto.desc())
                .all())

    @staticmethod
    def buscar_por_id(id_gasto: int) -> Gasto | None:
        return db.session.get(Gasto, id_gasto)

    @staticmethod
    def criar_gasto(dados: dict) -> Gasto:
        gasto = Gasto(
            valor=dados['valor'],
            data_gasto=dados['data_gasto'],
            forma_pagamento=dados['forma_pagamento'],
            descricao=dados.get('descricao'),
            id_usuario=dados['id_usuario'],
            id_categoria=dados['id_categoria']
        )
        db.session.add(gasto)
        db.session.commit()
        return gasto

    @staticmethod
    def atualizar_gasto(id_gasto: int, dados: dict) -> Gasto:
        gasto = GastoService.buscar_por_id(id_gasto)
        gasto.valor = dados['valor']
        gasto.data_gasto = dados['data_gasto']
        gasto.forma_pagamento = dados['forma_pagamento']
        gasto.descricao = dados.get('descricao')
        gasto.id_categoria = dados['id_categoria']
        db.session.commit()
        return gasto

    @staticmethod
    def excluir_gasto(id_gasto: int) -> None:
        gasto = GastoService.buscar_por_id(id_gasto)
        if gasto:
            db.session.delete(gasto)
            db.session.commit()

    @staticmethod
    def calcular_total(gastos: list[Gasto]) -> float:
        return sum(g.valor for g in gastos)

    @staticmethod
    def agrupar_por_categoria(gastos: list[Gasto]) -> dict:
        resultado = {}
        for gasto in gastos:
            nome = gasto.categoria.nome_categoria
            resultado[nome] = resultado.get(nome, 0) + gasto.valor
        return resultado
