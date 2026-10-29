import calendar
import uuid
from datetime import date
from decimal import Decimal
from app import db
from app.models.gasto import Gasto
from sqlalchemy import func, extract


class GastoService:
    """Regras de negócio relacionadas ao Gasto."""

    @staticmethod
    def _adicionar_meses(data_base: date, meses: int) -> date:
        """Adiciona N meses a uma data respeitando o último dia do mês (sem bibliotecas externas)."""
        mes = data_base.month - 1 + meses
        ano = data_base.year + mes // 12
        mes = mes % 12 + 1
        dia = min(data_base.day, calendar.monthrange(ano, mes)[1])
        return date(ano, mes, dia)

    @staticmethod
    def listar_por_usuario(id_usuario: int, busca: str = None, id_categoria: int = None,
                           apenas_parcelados: bool = False) -> list[Gasto]:
        q = Gasto.query.filter_by(id_usuario=id_usuario)
        if busca:
            q = q.filter(Gasto.descricao.ilike(f'%{busca}%'))
        if id_categoria:
            q = q.filter_by(id_categoria=id_categoria)
        if apenas_parcelados:
            q = q.filter(Gasto.total_parcelas > 1)
        return q.order_by(Gasto.data_gasto.desc()).all()

    @staticmethod
    def listar_paginado(id_usuario: int, page: int = 1, per_page: int = 20,
                        mes: int = None, ano: int = None,
                        busca: str = None, id_categoria: int = None,
                        apenas_parcelados: bool = False):
        """Retorna objeto de paginação do SQLAlchemy."""
        q = Gasto.query.filter_by(id_usuario=id_usuario)
        if mes:
            q = q.filter(extract('month', Gasto.data_gasto) == mes)
        if ano:
            q = q.filter(extract('year', Gasto.data_gasto) == ano)
        if busca:
            q = q.filter(Gasto.descricao.ilike(f'%{busca}%'))
        if id_categoria:
            q = q.filter_by(id_categoria=id_categoria)
        if apenas_parcelados:
            q = q.filter(Gasto.total_parcelas > 1)
        return q.order_by(Gasto.data_gasto.desc()).paginate(page=page, per_page=per_page, error_out=False)

    @staticmethod
    def filtrar_por_periodo(id_usuario: int, mes: int, ano: int,
                            busca: str = None, id_categoria: int = None,
                            apenas_parcelados: bool = False) -> list[Gasto]:
        """Filtra gastos por mês e ano usando extract (compatível com SQLite e PostgreSQL)."""
        q = (Gasto.query
             .filter_by(id_usuario=id_usuario)
             .filter(extract('month', Gasto.data_gasto) == mes)
             .filter(extract('year', Gasto.data_gasto) == ano))
        if busca:
            q = q.filter(Gasto.descricao.ilike(f'%{busca}%'))
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
            valor=dados['valor'],
            data_gasto=dados['data_gasto'],
            forma_pagamento=dados['forma_pagamento'],
            descricao=dados.get('descricao'),
            parcela_atual=dados.get('parcela_atual', 1),
            total_parcelas=dados.get('total_parcelas', 1),
            id_parcelamento=dados.get('id_parcelamento'),
            comprovante=dados.get('comprovante'),
            id_usuario=dados['id_usuario'],
            id_categoria=dados['id_categoria']
        )
        db.session.add(gasto)
        db.session.commit()
        return gasto

    @staticmethod
    def atualizar_gasto(id_gasto: int, dados: dict) -> Gasto | None:
        gasto = GastoService.buscar_por_id(id_gasto)
        if not gasto:
            return None
        gasto.valor = dados['valor']
        gasto.data_gasto = dados['data_gasto']
        gasto.forma_pagamento = dados['forma_pagamento']
        gasto.descricao = dados.get('descricao')
        gasto.id_categoria = dados['id_categoria']
        if 'comprovante' in dados and dados['comprovante']:
            gasto.comprovante = dados['comprovante']
        db.session.commit()
        return gasto

    @staticmethod
    def criar_gasto_parcelado(dados: dict, numero_parcelas: int) -> list[Gasto]:
        """Cria uma série de lançamentos distribuídos mensalmente para compras parceladas."""
        if numero_parcelas <= 1:
            return [GastoService.criar_gasto(dados)]

        valor_total = Decimal(str(dados['valor']))
        qtd_parc = Decimal(str(numero_parcelas))
        valor_base = round(valor_total / qtd_parc, 2)
        # Ajusta centavos restantes na primeira parcela para exatidão matemática
        diferenca = valor_total - (valor_base * qtd_parc)

        id_parcelamento = dados.get('id_parcelamento') or str(uuid.uuid4())
        data_inicial = dados['data_gasto']
        descricao_base = (dados.get('descricao') or 'Despesa').strip()
        comprovante = dados.get('comprovante')

        gastos_criados = []
        for i in range(1, numero_parcelas + 1):
            val_parcela = valor_base + (diferenca if i == 1 else Decimal('0.00'))
            data_parcela = GastoService._adicionar_meses(data_inicial, i - 1)
            desc_parcela = f"{descricao_base} ({i}/{numero_parcelas})"

            gasto = Gasto(
                valor=val_parcela,
                data_gasto=data_parcela,
                forma_pagamento=dados['forma_pagamento'],
                descricao=desc_parcela,
                parcela_atual=i,
                total_parcelas=numero_parcelas,
                id_parcelamento=id_parcelamento,
                comprovante=comprovante,
                id_usuario=dados['id_usuario'],
                id_categoria=dados['id_categoria']
            )
            db.session.add(gasto)
            gastos_criados.append(gasto)

        db.session.commit()
        return gastos_criados

    @staticmethod
    def excluir_gasto(id_gasto: int, excluir_todas_parcelas: bool = False) -> int:
        """Exclui um gasto ou todo o grupo de parcelas associadas."""
        gasto = GastoService.buscar_por_id(id_gasto)
        if not gasto:
            return 0

        from app.services.comprovante_service import remover_arquivo_comprovante
        comprovante = gasto.comprovante

        if excluir_todas_parcelas and gasto.id_parcelamento:
            qtd = Gasto.query.filter_by(
                id_parcelamento=gasto.id_parcelamento,
                id_usuario=gasto.id_usuario
            ).delete()
            db.session.commit()
            if comprovante:
                remover_arquivo_comprovante(comprovante)
            return qtd

        db.session.delete(gasto)
        db.session.commit()
        if comprovante:
            remover_arquivo_comprovante(comprovante)
        return 1

    @staticmethod
    def buscar_projecao_parcelas_futuras(id_usuario: int, meses_a_frente: int = 6) -> list[dict]:
        """Calcula o montante de parcelas já comprometidas para os próximos meses."""
        hoje = date.today()
        projecoes = []
        for i in range(1, meses_a_frente + 1):
            dt = GastoService._adicionar_meses(hoje, i)
            gastos_parc = (Gasto.query
                           .filter_by(id_usuario=id_usuario)
                           .filter(extract('month', Gasto.data_gasto) == dt.month)
                           .filter(extract('year', Gasto.data_gasto) == dt.year)
                           .filter(Gasto.total_parcelas > 1)
                           .all())
            total_parc = sum(g.valor for g in gastos_parc) if gastos_parc else Decimal('0')
            projecoes.append({
                'mes': dt.month,
                'ano': dt.year,
                'total_parcelas': float(total_parc),
                'qtd_parcelas': len(gastos_parc)
            })
        return projecoes


    @staticmethod
    def calcular_total(gastos: list[Gasto]) -> Decimal:
        return sum(g.valor for g in gastos) if gastos else Decimal('0')

    @staticmethod
    def calcular_total_periodo_sql(id_usuario: int, mes: int, ano: int) -> Decimal:
        """Calcula total diretamente no banco (mais eficiente)."""
        result = (db.session.query(func.sum(Gasto.valor))
                  .filter_by(id_usuario=id_usuario)
                  .filter(extract('month', Gasto.data_gasto) == mes)
                  .filter(extract('year', Gasto.data_gasto) == ano)
                  .scalar())
        return result or Decimal('0')

    @staticmethod
    def agrupar_por_categoria(gastos: list[Gasto]) -> dict:
        resultado = {}
        for gasto in gastos:
            nome = gasto.categoria.nome_categoria
            resultado[nome] = resultado.get(nome, Decimal('0')) + gasto.valor
        return resultado

    @staticmethod
    def evolucao_mensal(id_usuario: int, num_meses: int = 6) -> list[dict]:
        """Retorna total de gastos dos últimos N meses para gráfico de linha."""
        from datetime import date
        hoje = date.today()
        resultado = []
        for i in range(num_meses - 1, -1, -1):
            ano = hoje.year
            mes = hoje.month - i
            while mes <= 0:
                mes += 12
                ano -= 1
            total = GastoService.calcular_total_periodo_sql(id_usuario, mes, ano)
            resultado.append({'mes': mes, 'ano': ano, 'total': float(total)})
        return resultado

    @staticmethod
    def detectar_anomalias(id_usuario: int, mes: int, ano: int, meses_historico: int = 3) -> list[dict]:
        """Detecta categorias com gasto acima de 20% da média histórica."""
        gastos_atual = GastoService.filtrar_por_periodo(id_usuario, mes, ano)
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
                gs = GastoService.filtrar_por_periodo(id_usuario, m, a)
                ag = GastoService.agrupar_por_categoria(gs)
                if cat_nome in ag:
                    totais_hist.append(float(ag[cat_nome]))
            if len(totais_hist) >= 2:
                media = sum(totais_hist) / len(totais_hist)
                if media > 0 and float(total_atual) > media * 1.2:
                    pct = ((float(total_atual) - media) / media) * 100
                    alertas.append({
                        'categoria': cat_nome,
                        'total_atual': float(total_atual),
                        'media_historica': round(media, 2),
                        'variacao_pct': round(pct, 1)
                    })
        return alertas
