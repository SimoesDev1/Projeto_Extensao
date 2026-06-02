from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from app.services.usuario_service import UsuarioService
from app.utils import login_required

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/', methods=['GET'])
def home():
    return redirect(url_for('auth.login'))


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        nome_usuario = request.form.get('nome_usuario').strip().lower()
        senha = request.form.get('senha')

        usuario = UsuarioService.autenticar(nome_usuario, senha)

        if usuario:
            session['id_usuario'] = usuario.id_usuario
            session['nome_usuario'] = usuario.nome_usuario
            return redirect(url_for('dashboard.index'))

        flash('Usuário ou senha inválidos.', 'danger')

    return render_template('auth/login.html')


@auth_bp.route('/cadastro', methods=['GET', 'POST'])
def cadastro():
    if request.method == 'POST':
        nome_usuario = request.form.get('nome_usuario').strip().lower()
        senha = request.form.get('senha')

        if UsuarioService.nome_ja_existe(nome_usuario):
            flash('Usuário já existe.', 'danger')
            return render_template('auth/cadastro.html')

        UsuarioService.criar_usuario(nome_usuario, senha)
        flash('Cadastro realizado! Faça login.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/cadastro.html')


@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))


@auth_bp.route('/perfil', methods=['GET', 'POST'])
@login_required
def perfil():
    usuario = UsuarioService.buscar_por_id(session['id_usuario'])
    if request.method == 'POST':
        novo_nome = request.form.get('nome_usuario').strip().lower()
        if novo_nome != usuario.nome_usuario and UsuarioService.nome_ja_existe(novo_nome):
            flash('Este nome de usuário já está em uso.', 'danger')
        else:
            UsuarioService.atualizar_usuario(session['id_usuario'], {'nome_usuario': novo_nome})
            session['nome_usuario'] = novo_nome
            flash('Perfil atualizado com sucesso!', 'success')
            return redirect(url_for('auth.perfil'))
    
    return render_template('auth/perfil.html', usuario=usuario)