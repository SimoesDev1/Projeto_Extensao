from app.models.categoria import Categoria


class CategoriaService:
    """Regras de negócio relacionadas à Categoria."""

    @staticmethod
    def listar_todas() -> list[Categoria]:
        return Categoria.query.order_by(Categoria.nome_categoria).all()
