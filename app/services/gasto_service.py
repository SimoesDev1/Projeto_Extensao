
import calendar
import uuid
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import extract, func

from app import db
from app.models.gasto import Gasto


class GastoService:
    """Regras de negócio relacionadas ao Gasto."""

    @staticmethod
    def _adicionar_meses(data_base: date, meses: int) -> date:
        """Adiciona meses respeitando o último dia do mês."""
        mes = data_base.month - 1 + meses
        ano = data_base.year + mes // 12
        mes = mes % 12 + 1
        dia = min(data_base.day, calendar.monthrange(ano, mes)[1])
        return date(ano, mes, dia)

    @staticmethod
    def _valor_decimal(valor) -> Decimal:
        """Converte valores para Decimal com precisão de centavos."""
        return Decimal(str(valor)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP
        )

    @staticmethod
    def listar_por_usuario(
        id_usuario: int,
        busca: str = None,
        id_categoria: int = None,
        apenas_parcelados: bool = False
    ) -> list[Gasto]:
        q = Gasto.query.filter_by(id_usuario=id_usuario)

        if busca:
            q = q.filter(Gasto.descricao.ilike(f"%{busca}%"))

        if id_categoria:
            q = q.filter_by(id_categoria=id_categoria)

        if apenas_parcelados:
            q = q.filter(Gasto.total_parcelas > 1)

        return q.order_by(Gasto.data_gasto.desc()).all()

    @staticmethod
    def listar_paginado(
        id_usuario: int,
        page: int = 1,
        per_page: int = 20,
        mes: int = None,
        ano: int = None,
        busca: str = None,
        id_categoria: int = None,
        apenas_parcelados: bool = False
    ):
        q = Gasto.query.filter_by(id_usuario=id_usuario)

        if mes:
            q = q.filter(extract("month", Gasto.data_gasto) == mes)

        if ano:
            q = q.filter(extract("year", Gasto.data_gasto) == ano)

        if busca:
            q = q.filter(Gasto.descricao.ilike(f"%{busca}%"))

        if id_categoria:
            q = q.filter_by(id_categoria=id_categoria)

        if apenas_parcelados:
            q = q.filter(Gasto.total_parcelas > 1)

        return q.order_by(Gasto.data_gasto.desc()).paginate(
            page=page,
            per_page=per_page,
            error_out=False
        )

    @staticmethod
    def filtrar_por_periodo(
        id_usuario: int,
        mes: int,
        ano: int,
        busca: str = None,
        id_categoria: int = None,
        apenas_parcelados: bool = False
    ) -> list[Gasto]:
        q = (
            Gasto.query
            .filter_by(id_usuario=id_usuario)
            .filter(extract("month", Gasto.data_gasto) == mes)
            .filter(extract("year", Gasto.data_gasto) == ano)
        )

        if busca:
            q = q.filter(Gasto.descricao.ilike(f"%{busca}%"))

        if id_categoria:
            q = q.filter_by(id_categoria=id_categoria)

        if apenas_parcelados:
            q = q.filter(Gasto.total_parcelas > 1)

        return q.order_by(Gasto.data_gasto.desc()).all()

    @staticmethod
    def buscar_por_id(id_gasto: int) -> Gasto | None:
        return db.session.get(Gasto, id_gasto)

    @staticmethod
    def criar_gasto(dados: dict) -> Gasto:
        gasto = Gasto(
            valor=GastoService._valor_decimal(dados["valor"]),
            data_gasto=dados["data_gasto"],
            forma_pagamento=dados["forma_pagamento"],
            descricao=dados.get("descricao"),
            parcela_atual=dados.get("parcela_atual", 1),
            total_parcelas=dados.get("total_parcelas", 1),
            id_parcelamento=dados.get("id_parcelamento"),
            comprovante=dados.get("comprovante"),
            id_usuario=dados["id_usuario"],
            id_categoria=dados["id_categoria"]
        )

        try:
            db.session.add(gasto)
            db.session.commit()
            return gasto
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def atualizar_gasto(id_gasto: int, dados: dict) -> Gasto | None:
        gasto = GastoService.buscar_por_id(id_gasto)

        if not gasto:
            return None

        try:
            gasto.valor = GastoService._valor_decimal(dados["valor"])
            gasto.data_gasto = dados["data_gasto"]
            gasto.forma_pagamento = dados["forma_pagamento"]
            gasto.descricao = dados.get("descricao")
            gasto.id_categoria = dados["id_categoria"]

            if "comprovante" in dados and dados["comprovante"]:
                gasto.comprovante = dados["comprovante"]

            db.session.commit()
            return gasto
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def criar_gasto_parcelado(
        dados: dict,
        numero_parcelas: int
    ) -> list[Gasto]:
        """Cria lançamentos mensais com soma exata do valor total."""

        if not isinstance(numero_parcelas, int) or numero_parcelas < 1:
            raise ValueError("O número de parcelas deve ser um inteiro positivo.")

        if numero_parcelas == 1:
            return [GastoService.criar_gasto(dados)]

        if numero_parcelas > 36:
            raise ValueError("O número máximo permitido é 36 parcelas.")

        valor_total = GastoService._valor_decimal(dados["valor"])

        if valor_total <= 0:
            raise ValueError("O valor do gasto deve ser maior que zero.")

        qtd_parcelas = Decimal(numero_parcelas)

        valor_base = (valor_total / qtd_parcelas).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP
        )

        diferenca = valor_total - (valor_base * qtd_parcelas)

        id_parcelamento = (
            dados.get("id_parcelamento") or str(uuid.uuid4())
        )

        data_inicial = dados["data_gasto"]
        descricao_base = (dados.get("descricao") or "Despesa").strip()

        gastos_criados = []

        try:
            for i in range(1, numero_parcelas + 1):
                valor_parcela = (
                    valor_base + diferenca
                    if i == 1
                    else valor_base
                )

                data_parcela = GastoService._adicionar_meses(
                    data_inicial,
                    i - 1
                )

                gasto = Gasto(
                    valor=valor_parcela,
                    data_gasto=data_parcela,
                    forma_pagamento=dados["forma_pagamento"],
                    descricao=f"{descricao_base} ({i}/{numero_parcelas})",
                    parcela_atual=i,
                    total_parcelas=numero_parcelas,
                    id_parcelamento=id_parcelamento,
                    comprovante=dados.get("comprovante"),
                    id_usuario=dados["id_usuario"],
                    id_categoria=dados["id_categoria"]
                )

                db.session.add(gasto)
                gastos_criados.append(gasto)

            db.session.commit()
            return gastos_criados

        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def excluir_gasto(
        id_gasto: int,
        excluir_todas_parcelas: bool = False
    ) -> int:
        """Exclui um gasto ou todas as parcelas do mesmo grupo."""

        gasto = GastoService.buscar_por_id(id_gasto)

        if not gasto:
            return 0

        from app.services.comprovante_service import (
            remover_arquivo_comprovante
        )

        comprovante = gasto.comprovante

        try:
            if excluir_todas_parcelas and gasto.id_parcelamento:
                qtd = Gasto.query.filter_by(
                    id_parcelamento=gasto.id_parcelamento,
                    id_usuario=gasto.id_usuario
                ).delete(synchronize_session=False)
            else:
                db.session.delete(gasto)
                qtd = 1

            db.session.commit()

        except Exception:
            db.session.rollback()
            raise

        # A função verifica se ainda existem referências no banco.
        if comprovante:
            remover_arquivo_comprovante(comprovante)

        return qtd

    @staticmethod
    def buscar_projecao_parcelas_futuras(
        id_usuario: int,
        meses_a_frente: int = 6
    ) -> list[dict]:
        hoje = date.today()
        projecoes = []

        for i in range(1, meses_a_frente + 1):
            dt = GastoService._adicionar_meses(hoje, i)

            gastos_parc = (
                Gasto.query
                .filter_by(id_usuario=id_usuario)
                .filter(extract("month", Gasto.data_gasto) == dt.month)
                .filter(extract("year", Gasto.data_gasto) == dt.year)
                .filter(Gasto.total_parcelas > 1)
                .all()
            )

            total_parc = sum(
                (GastoService._valor_decimal(g.valor) for g in gastos_parc),
                Decimal("0.00")
            )

            projecoes.append({
                "mes": dt.month,
                "ano": dt.year,
                "total_parcelas": float(total_parc),
                "qtd_parcelas": len(gastos_parc)
            })

        return projecoes

    @staticmethod
    def calcular_total(gastos: list[Gasto]) -> Decimal:
        return sum(
            (GastoService._valor_decimal(g.valor) for g in gastos),
            Decimal("0.00")
        )

    @staticmethod
    def calcular_total_periodo_sql(
        id_usuario: int,
        mes: int,
        ano: int
    ) -> Decimal:
        result = (
            db.session.query(func.sum(Gasto.valor))
            .filter_by(id_usuario=id_usuario)
            .filter(extract("month", Gasto.data_gasto) == mes)
            .filter(extract("year", Gasto.data_gasto) == ano)
            .scalar()
        )

        return result or Decimal("0.00")

    @staticmethod
    def agrupar_por_categoria(gastos: list[Gasto]) -> dict:
        resultado = {}

        for gasto in gastos:
            categoria = getattr(gasto, "categoria", None)
            nome = (
                getattr(categoria, "nome_categoria", None)
                if categoria
                else None
            ) or "Sem categoria"

            resultado[nome] = (
                resultado.get(nome, Decimal("0.00"))
                + GastoService._valor_decimal(gasto.valor)
            )

        return resultado

    @staticmethod
    def evolucao_mensal(
        id_usuario: int,
        num_meses: int = 6
    ) -> list[dict]:
        hoje = date.today()
        resultado = []

        for i in range(num_meses - 1, -1, -1):
            ano = hoje.year
            mes = hoje.month - i

            while mes <= 0:
                mes += 12
                ano -= 1

            total = GastoService.calcular_total_periodo_sql(
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

    @staticmethod
    def detectar_anomalias(
        id_usuario: int,
        mes: int,
        ano: int,
        meses_historico: int = 3
    ) -> list[dict]:
        """Detecta categorias com gasto acima de 20% da média histórica."""

        gastos_atual = GastoService.filtrar_por_periodo(
            id_usuario,
            mes,
            ano
        )

        por_cat_atual = GastoService.agrupar_por_categoria(gastos_atual)
        alertas = []

        for cat_nome, total_atual in por_cat_atual.items():
            totais_hist = []

            for i in range(1, meses_historico + 1):
                a = ano
                m = mes - i

                while m <= 0:
                    m += 12
                    a -= 1

                gs = GastoService.filtrar_por_periodo(
                    id_usuario,
                    m,
                    a
                )

                ag = GastoService.agrupar_por_categoria(gs)

                if cat_nome in ag:
                    totais_hist.append(float(ag[cat_nome]))

            if len(totais_hist) >= 2:
                media = sum(totais_hist) / len(totais_hist)

                if media > 0 and float(total_atual) > media * 1.2:
                    pct = (
                        (float(total_atual) - media) / media
                    ) * 100

                    alertas.append({
                        "categoria": cat_nome,
                        "total_atual": float(total_atual),
                        "media_historica": round(media, 2),
                        "variacao_pct": round(pct, 1)
                    })

        return alertas
