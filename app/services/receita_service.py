from app import db
from app.models.receita import Receita
from sqlalchemy import func, extract

class ReceitaService:
    """Regras de negócio relacionadas às Receitas."""

    @staticmethod
    def listar_por_usuario(id_usuario: int) -> list[Receita]:
        return (Receita.query
                .filter_by(id_usuario=id_usuario)
                .order_by(Receita.data_receita.desc())
                .all())

    @staticmethod
    def filtrar_por_periodo(id_usuario: int, mes: int, ano: int) -> list[Receita]:
        return (Receita.query
                .filter_by(id_usuario=id_usuario)
                .filter(extract('month', Receita.data_receita) == mes)
                .filter(extract('year', Receita.data_receita) == ano)
                .order_by(Receita.data_receita.desc())
                .all())

    @staticmethod
    def buscar_por_id(id_receita: int) -> Receita | None:
        return db.session.get(Receita, id_receita)

    @staticmethod
    def criar_receita(dados: dict) -> Receita:
        receita = Receita(
            valor=dados['valor'],
            data_receita=dados['data_receita'],
            forma_recebimento=dados['forma_recebimento'],
            descricao=dados.get('descricao'),
            id_usuario=dados['id_usuario']
        )
        db.session.add(receita)
        db.session.commit()
        return receita

    @staticmethod
    def atualizar_receita(id_receita: int, dados: dict) -> Receita:
        receita = ReceitaService.buscar_por_id(id_receita)
        receita.valor = dados['valor']
        receita.data_receita = dados['data_receita']
        receita.forma_recebimento = dados['forma_recebimento']
        receita.descricao = dados.get('descricao')
        db.session.commit()
        return receita

    @staticmethod
    def excluir_receita(id_receita: int) -> None:
        receita = ReceitaService.buscar_por_id(id_receita)
        if receita:
            db.session.delete(receita)
            db.session.commit()

    @staticmethod
    def calcular_total(receitas: list[Receita]) -> float:
        return sum(r.valor for r in receitas)
