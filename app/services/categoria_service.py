from app import db
from app.models.categoria import Categoria


class CategoriaService:
    """Regras de negócio relacionadas à Categoria."""

    @staticmethod
    def listar_todas() -> list[Categoria]:
        return Categoria.query.order_by(Categoria.nome_categoria).all()

    @staticmethod
    def buscar_por_id(id_categoria: int) -> Categoria | None:
        return Categoria.query.get(id_categoria)

    @staticmethod
    def criar_categoria(nome_categoria: str) -> Categoria:
        categoria = Categoria(nome_categoria=nome_categoria)
        db.session.add(categoria)
        db.session.commit()
        return categoria
