from decimal import Decimal
from app import db
from app.models.receita import Receita
from sqlalchemy import func, extract

class ReceitaService:
    """Regras de negócio relacionadas às Receitas."""

    @staticmethod
    def listar_por_usuario(id_usuario: int, busca: str = None) -> list[Receita]:
        q = Receita.query.filter_by(id_usuario=id_usuario)
        if busca:
            q = q.filter(Receita.descricao.ilike(f'%{busca}%'))
        return q.order_by(Receita.data_receita.desc()).all()

    @staticmethod
    def listar_paginado(id_usuario: int, page: int = 1, per_page: int = 20,
                        mes: int = None, ano: int = None, busca: str = None):
        """Retorna objeto de paginação do SQLAlchemy."""
        q = Receita.query.filter_by(id_usuario=id_usuario)
        if mes:
            q = q.filter(extract('month', Receita.data_receita) == mes)
        if ano:
            q = q.filter(extract('year', Receita.data_receita) == ano)
        if busca:
            q = q.filter(Receita.descricao.ilike(f'%{busca}%'))
        return q.order_by(Receita.data_receita.desc()).paginate(page=page, per_page=per_page, error_out=False)

    @staticmethod
    def filtrar_por_periodo(id_usuario: int, mes: int, ano: int, busca: str = None) -> list[Receita]:
        q = (Receita.query
             .filter_by(id_usuario=id_usuario)
             .filter(extract('month', Receita.data_receita) == mes)
             .filter(extract('year', Receita.data_receita) == ano))
        if busca:
            q = q.filter(Receita.descricao.ilike(f'%{busca}%'))
        return q.order_by(Receita.data_receita.desc()).all()

    @staticmethod
    def buscar_por_id(id_receita: int) -> Receita | None:
        return db.session.get(Receita, id_receita)

    @staticmethod
    def criar_receita(dados: dict) -> Receita:
        receita = Receita(
            valor=dados['valor'],
            data_receita=dados['data_receita'],
            forma_recebimento=dados['forma_recebimento'],
            tipo_receita=dados.get('tipo_receita', 'Venda'),
            descricao=dados.get('descricao'),
            comprovante=dados.get('comprovante'),
            id_usuario=dados['id_usuario']
        )
        db.session.add(receita)
        db.session.commit()
        return receita

    @staticmethod
    def atualizar_receita(id_receita: int, dados: dict) -> Receita:
        receita = ReceitaService.buscar_por_id(id_receita)
        if not receita:
            return None
        receita.valor = dados['valor']
        receita.data_receita = dados['data_receita']
        receita.forma_recebimento = dados['forma_recebimento']
        receita.tipo_receita = dados.get('tipo_receita', receita.tipo_receita)
        receita.descricao = dados.get('descricao')
        if 'comprovante' in dados and dados['comprovante']:
            receita.comprovante = dados['comprovante']
        db.session.commit()
        return receita

    @staticmethod
    def excluir_receita(id_receita: int) -> None:
        receita = ReceitaService.buscar_por_id(id_receita)
        if receita:
            db.session.delete(receita)
            db.session.commit()

    @staticmethod
    def calcular_total(receitas: list[Receita]) -> Decimal:
        return sum(r.valor for r in receitas) if receitas else Decimal('0')

    @staticmethod
    def calcular_total_periodo_sql(id_usuario: int, mes: int, ano: int) -> Decimal:
        """Calcula total diretamente no banco."""
        result = (db.session.query(func.sum(Receita.valor))
                  .filter_by(id_usuario=id_usuario)
                  .filter(extract('month', Receita.data_receita) == mes)
                  .filter(extract('year', Receita.data_receita) == ano)
                  .scalar())
        return result or Decimal('0')

    @staticmethod
    def agrupar_por_tipo(receitas: list[Receita]) -> dict:
        """Agrupa receitas por tipo para exibição em gráfico."""
        resultado = {}
        for r in receitas:
            tipo = r.tipo_receita or 'Outros'
            resultado[tipo] = resultado.get(tipo, Decimal('0')) + r.valor
        return resultado

    @staticmethod
    def evolucao_mensal(id_usuario: int, num_meses: int = 6) -> list[dict]:
        """Retorna total de receitas dos últimos N meses para gráfico de linha."""
        from datetime import date
        hoje = date.today()
        resultado = []
        for i in range(num_meses - 1, -1, -1):
            ano = hoje.year
            mes = hoje.month - i
            while mes <= 0:
                mes += 12
                ano -= 1
            total = ReceitaService.calcular_total_periodo_sql(id_usuario, mes, ano)
            resultado.append({'mes': mes, 'ano': ano, 'total': float(total)})
        return resultado
