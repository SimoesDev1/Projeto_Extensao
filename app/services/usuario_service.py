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

    @staticmethod
    def buscar_por_id(id_usuario: int) -> Usuario | None:
        return db.session.get(Usuario, id_usuario)

    @staticmethod
    def atualizar_usuario(id_usuario: int, novos_dados: dict) -> Usuario:
        usuario = UsuarioService.buscar_por_id(id_usuario)
        if 'nome_usuario' in novos_dados:
            usuario.nome_usuario = UsuarioService._normalizar_nome(novos_dados['nome_usuario'])
        db.session.commit()
        return usuario

    @staticmethod
    def alterar_senha(id_usuario: int, senha_atual: str, nova_senha: str) -> tuple[bool, str]:
        """Altera a senha do usuário após verificar a senha atual. Retorna (sucesso, mensagem)."""
        usuario = UsuarioService.buscar_por_id(id_usuario)
        if not usuario:
            return False, 'Usuário não encontrado.'
        if not usuario.verificar_senha(senha_atual):
            return False, 'Senha atual incorreta.'
        if len(nova_senha) < 6:
            return False, 'A nova senha deve ter pelo menos 6 caracteres.'
        usuario.set_senha(nova_senha)
        db.session.commit()
        return True, 'Senha alterada com sucesso!'