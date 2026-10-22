import logging
from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from app import limiter
from app.services.usuario_service import UsuarioService
from app.utils import login_required

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/', methods=['GET'])
def home():
    if session.get('id_usuario'):
        return redirect(url_for('dashboard.index'))
    return redirect(url_for('auth.login'))


@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("15 per minute", error_message="Muitas tentativas. Aguarde 1 minuto.")
def login():
    if session.get('id_usuario'):
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        nome_usuario = request.form.get('nome_usuario', '').strip().lower()
        senha = request.form.get('senha', '')

        usuario = UsuarioService.autenticar(nome_usuario, senha)

        if usuario:
            session.permanent = True  # Ativa expiração configurada em Config
            session['id_usuario'] = usuario.id_usuario
            session['nome_usuario'] = usuario.nome_usuario
            logger.info(f"Login: usuário '{nome_usuario}' autenticado.")
            next_url = request.args.get('next') or request.form.get('next')
            if next_url and next_url.startswith('/') and not next_url.startswith('//'):
                return redirect(next_url)
            return redirect(url_for('dashboard.index'))

        logger.warning(f"Login falhou para usuário '{nome_usuario}'.")
        flash('Usuário ou senha inválidos.', 'danger')

    return render_template('auth/login.html')


@auth_bp.route('/cadastro', methods=['GET', 'POST'])
@limiter.limit("10 per minute", error_message="Muitas tentativas. Aguarde 1 minuto.")
def cadastro():
    if session.get('id_usuario'):
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        nome_usuario = request.form.get('nome_usuario', '').strip().lower()
        senha = request.form.get('senha', '')

        if not nome_usuario:
            flash('O nome de usuário é obrigatório.', 'danger')
            return render_template('auth/cadastro.html')

        if len(senha) < 6:
            flash('A senha deve ter pelo menos 6 caracteres.', 'danger')
            return render_template('auth/cadastro.html')

        if UsuarioService.nome_ja_existe(nome_usuario):
            flash('Usuário já existe.', 'danger')
            return render_template('auth/cadastro.html')

        UsuarioService.criar_usuario(nome_usuario, senha)
        logger.info(f"Novo usuário criado: '{nome_usuario}'.")
        flash('Cadastro realizado! Faça login.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/cadastro.html')


@auth_bp.route('/logout')
def logout():
    nome = session.get('nome_usuario', '?')
    session.clear()
    logger.info(f"Logout: usuário '{nome}'.")
    return redirect(url_for('auth.login'))


@auth_bp.route('/perfil', methods=['GET', 'POST'])
@login_required
def perfil():
    usuario = UsuarioService.buscar_por_id(session['id_usuario'])

    if request.method == 'POST':
        acao = request.form.get('acao')

        if acao == 'atualizar_nome':
            novo_nome = request.form.get('nome_usuario', '').strip().lower()
            if not novo_nome:
                flash('O nome de usuário é obrigatório.', 'danger')
            elif novo_nome != usuario.nome_usuario and UsuarioService.nome_ja_existe(novo_nome):
                flash('Este nome de usuário já está em uso.', 'danger')
            else:
                UsuarioService.atualizar_usuario(session['id_usuario'], {'nome_usuario': novo_nome})
                session['nome_usuario'] = novo_nome
                flash('Nome atualizado com sucesso!', 'success')
                return redirect(url_for('auth.perfil'))

        elif acao == 'alterar_senha':
            senha_atual = request.form.get('senha_atual', '')
            nova_senha = request.form.get('nova_senha', '')
            confirmar = request.form.get('confirmar_senha', '')

            if nova_senha != confirmar:
                flash('As senhas não coincidem.', 'danger')
            else:
                sucesso, mensagem = UsuarioService.alterar_senha(
                    session['id_usuario'], senha_atual, nova_senha
                )
                flash(mensagem, 'success' if sucesso else 'danger')
                if sucesso:
                    return redirect(url_for('auth.perfil'))

    return render_template('auth/perfil.html', usuario=usuario)