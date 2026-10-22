import logging
from flask import Blueprint, render_template, redirect, url_for, request, flash
from app import db
from app.models.categoria import Categoria
from app.services.categoria_service import CategoriaService
from app.utils import login_required

logger = logging.getLogger(__name__)
categorias_bp = Blueprint('categorias', __name__, url_prefix='/categorias')


@categorias_bp.route('/')
@login_required
def listar():
    categorias = CategoriaService.listar_todas()
    return render_template('categorias/listar.html', categorias=categorias)


@categorias_bp.route('/nova', methods=['GET', 'POST'])
@login_required
def nova():
    if request.method == 'POST':
        nome = request.form.get('nome_categoria', '').strip()
        if not nome:
            flash('Nome da categoria é obrigatório.', 'danger')
        elif Categoria.query.filter_by(nome_categoria=nome).first():
            flash('Já existe uma categoria com esse nome.', 'danger')
        else:
            cat = Categoria(nome_categoria=nome)
            db.session.add(cat)
            db.session.commit()
            logger.info(f"Categoria criada: '{nome}'")
            flash(f'Categoria "{nome}" criada com sucesso!', 'success')
            return redirect(url_for('categorias.listar'))
    return render_template('categorias/nova.html')


@categorias_bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar(id):
    cat = Categoria.query.get_or_404(id)
    if request.method == 'POST':
        novo_nome = request.form.get('nome_categoria', '').strip()
        if not novo_nome:
            flash('Nome inválido.', 'danger')
        elif Categoria.query.filter(Categoria.nome_categoria == novo_nome,
                                    Categoria.id_categoria != id).first():
            flash('Já existe uma categoria com esse nome.', 'danger')
        else:
            cat.nome_categoria = novo_nome
            db.session.commit()
            flash('Categoria atualizada!', 'success')
            return redirect(url_for('categorias.listar'))
    return render_template('categorias/editar.html', categoria=cat)


@categorias_bp.route('/excluir/<int:id>', methods=['POST'])
@login_required
def excluir(id):
    cat = Categoria.query.get_or_404(id)
    if cat.gastos:
        flash(f'A categoria "{cat.nome_categoria}" possui despesas vinculadas e não pode ser excluída.', 'danger')
    elif cat.metas:
        flash(f'A categoria "{cat.nome_categoria}" possui metas vinculadas e não pode ser excluída.', 'danger')
    else:
        nome = cat.nome_categoria
        db.session.delete(cat)
        db.session.commit()
        flash(f'Categoria "{nome}" excluída.', 'success')
    return redirect(url_for('categorias.listar'))
