from decimal import Decimal
from app import db
from app.models.meta import Meta


class MetaService:
    """Regras de negócio relacionadas às Metas/Orçamentos por categoria."""

    @staticmethod
    def listar_por_usuario_periodo(id_usuario: int, mes: int, ano: int) -> list[Meta]:
        return (Meta.query
                .filter_by(id_usuario=id_usuario, mes=mes, ano=ano)
                .all())

    @staticmethod
    def buscar_por_id(id_meta: int) -> Meta | None:
        return db.session.get(Meta, id_meta)

    @staticmethod
    def buscar_meta(id_usuario: int, id_categoria: int, mes: int, ano: int) -> Meta | None:
        return Meta.query.filter_by(
            id_usuario=id_usuario,
            id_categoria=id_categoria,
            mes=mes,
            ano=ano
        ).first()

    @staticmethod
    def criar_ou_atualizar(id_usuario: int, id_categoria: int, mes: int, ano: int, valor_limite: float) -> Meta:
        meta = MetaService.buscar_meta(id_usuario, id_categoria, mes, ano)
        if meta:
            meta.valor_limite = valor_limite
        else:
            meta = Meta(
                id_usuario=id_usuario,
                id_categoria=id_categoria,
                mes=mes,
                ano=ano,
                valor_limite=valor_limite
            )
            db.session.add(meta)
        db.session.commit()
        return meta

    @staticmethod
    def excluir(id_meta: int) -> None:
        meta = MetaService.buscar_por_id(id_meta)
        if meta:
            db.session.delete(meta)
            db.session.commit()

    @staticmethod
    def calcular_progresso(metas: list[Meta], por_categoria: dict) -> list[dict]:
        """Retorna lista com meta, gasto real e percentual de uso."""
        resultado = []
        for meta in metas:
            cat_nome = meta.categoria.nome_categoria
            gasto_real = float(por_categoria.get(cat_nome, Decimal('0')))
            limite = float(meta.valor_limite)
            pct = round((gasto_real / limite) * 100, 1) if limite > 0 else 0
            resultado.append({
                'meta': meta,
                'cat_nome': cat_nome,
                'gasto_real': gasto_real,
                'limite': limite,
                'pct': min(pct, 100),
                'excedeu': gasto_real > limite
            })
        return resultado
