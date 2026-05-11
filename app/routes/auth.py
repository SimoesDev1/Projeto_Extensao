from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from app.services.usuario_service import UsuarioService

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
            return redirect(url_for('gastos.listar'))

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