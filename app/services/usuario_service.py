from app import db
from app.models.usuario import Usuario


class UsuarioService:
    """Regras de negócio relacionadas ao Usuário."""

    @staticmethod
    def _normalizar_nome(nome_usuario: str) -> str:
        return nome_usuario.strip().lower()

    @staticmethod
    def buscar_por_nome(nome_usuario: str) -> Usuario | None:
        nome_usuario = UsuarioService._normalizar_nome(nome_usuario)
        return Usuario.query.filter_by(nome_usuario=nome_usuario).first()

    @staticmethod
    def criar_usuario(nome_usuario: str, senha: str) -> Usuario:
        nome_usuario = UsuarioService._normalizar_nome(nome_usuario)
        usuario = Usuario(nome_usuario=nome_usuario)
        usuario.set_senha(senha)
        db.session.add(usuario)
        db.session.commit()
        return usuario

    @staticmethod
    def autenticar(nome_usuario: str, senha: str) -> Usuario | None:
        usuario = UsuarioService.buscar_por_nome(nome_usuario)
        if usuario and usuario.verificar_senha(senha):
            return usuario
        return None

    @staticmethod
    def nome_ja_existe(nome_usuario: str) -> bool:
        return UsuarioService.buscar_por_nome(nome_usuario) is not None