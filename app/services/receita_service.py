
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import extract, func

from app import db
from app.models.receita import Receita


class ReceitaService:
    """Regras de negócio relacionadas às Receitas."""

    @staticmethod
    def _valor_decimal(valor) -> Decimal:
        return Decimal(str(valor)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP
        )

    @staticmethod
    def listar_por_usuario(
        id_usuario: int,
        busca: str = None
    ) -> list[Receita]:
        q = Receita.query.filter_by(id_usuario=id_usuario)

        if busca:
            q = q.filter(Receita.descricao.ilike(f"%{busca}%"))

        return q.order_by(Receita.data_receita.desc()).all()

    @staticmethod
    def listar_paginado(
        id_usuario: int,
        page: int = 1,
        per_page: int = 20,
        mes: int = None,
        ano: int = None,
        busca: str = None
    ):
        q = Receita.query.filter_by(id_usuario=id_usuario)

        if mes:
            q = q.filter(extract("month", Receita.data_receita) == mes)

        if ano:
            q = q.filter(extract("year", Receita.data_receita) == ano)

        if busca:
            q = q.filter(Receita.descricao.ilike(f"%{busca}%"))

        return q.order_by(Receita.data_receita.desc()).paginate(
            page=page,
            per_page=per_page,
            error_out=False
        )

    @staticmethod
    def filtrar_por_periodo(
        id_usuario: int,
        mes: int,
        ano: int,
        busca: str = None
    ) -> list[Receita]:
        q = (
            Receita.query
            .filter_by(id_usuario=id_usuario)
            .filter(extract("month", Receita.data_receita) == mes)
            .filter(extract("year", Receita.data_receita) == ano)
        )

        if busca:
            q = q.filter(Receita.descricao.ilike(f"%{busca}%"))

        return q.order_by(Receita.data_receita.desc()).all()

    @staticmethod
    def buscar_por_id(id_receita: int) -> Receita | None:
        return db.session.get(Receita, id_receita)

    @staticmethod
    def criar_receita(dados: dict) -> Receita:
        receita = Receita(
            valor=ReceitaService._valor_decimal(dados["valor"]),
            data_receita=dados["data_receita"],
            forma_recebimento=dados["forma_recebimento"],
            tipo_receita=dados.get("tipo_receita", "Venda"),
            descricao=dados.get("descricao"),
            comprovante=dados.get("comprovante"),
            id_usuario=dados["id_usuario"]
        )

        try:
            db.session.add(receita)
            db.session.commit()
            return receita
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def atualizar_receita(
        id_receita: int,
        dados: dict
    ) -> Receita | None:
        receita = ReceitaService.buscar_por_id(id_receita)

        if not receita:
            return None

        try:
            receita.valor = ReceitaService._valor_decimal(dados["valor"])
            receita.data_receita = dados["data_receita"]
            receita.forma_recebimento = dados["forma_recebimento"]
            receita.tipo_receita = dados.get(
                "tipo_receita",
                receita.tipo_receita
            )
            receita.descricao = dados.get("descricao")

            if "comprovante" in dados and dados["comprovante"]:
                receita.comprovante = dados["comprovante"]

            db.session.commit()
            return receita

        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def excluir_receita(id_receita: int) -> None:
        receita = ReceitaService.buscar_por_id(id_receita)

        if not receita:
            return

        comprovante = receita.comprovante

        try:
            db.session.delete(receita)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

        if comprovante:
            from app.services.comprovante_service import (
                remover_arquivo_comprovante
            )
            remover_arquivo_comprovante(comprovante)

    @staticmethod
    def calcular_total(receitas: list[Receita]) -> Decimal:
        return sum(
            (ReceitaService._valor_decimal(r.valor) for r in receitas),
            Decimal("0.00")
        )

    @staticmethod
    def calcular_total_periodo_sql(
        id_usuario: int,
        mes: int,
        ano: int
    ) -> Decimal:
        result = (
            db.session.query(func.sum(Receita.valor))
            .filter_by(id_usuario=id_usuario)
            .filter(extract("month", Receita.data_receita) == mes)
            .filter(extract("year", Receita.data_receita) == ano)
            .scalar()
        )

        return result or Decimal("0.00")

    @staticmethod
    def agrupar_por_tipo(receitas: list[Receita]) -> dict:
        resultado = {}

        for receita in receitas:
            tipo = receita.tipo_receita or "Outros"

            resultado[tipo] = (
                resultado.get(tipo, Decimal("0.00"))
                + ReceitaService._valor_decimal(receita.valor)
            )

        return resultado

    @staticmethod
    def evolucao_mensal(
        id_usuario: int,
        num_meses: int = 6
    ) -> list[dict]:
        from datetime import date

        hoje = date.today()
        resultado = []

        for i in range(num_meses - 1, -1, -1):
            ano = hoje.year
            mes = hoje.month - i

            while mes <= 0:
                mes += 12
                ano -= 1

            total = ReceitaService.calcular_total_periodo_sql(
                id_usuario,
                mes,
                ano
            )

            resultado.append({
                "mes": mes,
                "ano": ano,
                "total": float(total)
            })

        return resultado
