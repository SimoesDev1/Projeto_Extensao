import logging
from datetime import date
from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from app.services.meta_service import MetaService
from app.services.gasto_service import GastoService
from app.services.categoria_service import CategoriaService
from app.utils import login_required

logger = logging.getLogger(__name__)
metas_bp = Blueprint('metas', __name__, url_prefix='/metas')


@metas_bp.route('/')
@login_required
def listar():
    hoje = date.today()
    id_usuario = session['id_usuario']
    mes = request.args.get('mes', type=int, default=hoje.month)
    ano = request.args.get('ano', type=int, default=hoje.year)

    categorias = CategoriaService.listar_todas()
    metas = MetaService.listar_por_usuario_periodo(id_usuario, mes, ano)

    # Calcula progresso de cada meta
    gastos = GastoService.filtrar_por_periodo(id_usuario, mes, ano)
    por_categoria = GastoService.agrupar_por_categoria(gastos)
    progresso = MetaService.calcular_progresso(metas, por_categoria)

    return render_template(
        'metas/listar.html',
        categorias=categorias, progresso=progresso,
        mes=mes, ano=ano
    )


@metas_bp.route('/salvar', methods=['POST'])
@login_required
def salvar():
    """Cria ou atualiza uma meta."""
    id_usuario = session['id_usuario']
    try:
        id_categoria = int(request.form.get('id_categoria', 0))
        mes = int(request.form.get('mes'))
        ano = int(request.form.get('ano'))
        valor_limite = float(request.form.get('valor_limite', 0))

        if id_categoria <= 0:
            flash('Selecione uma categoria válida.', 'danger')
        elif valor_limite <= 0:
            flash('O valor limite deve ser maior que zero.', 'danger')
        else:
            MetaService.criar_ou_atualizar(id_usuario, id_categoria, mes, ano, valor_limite)
            flash('Meta salva com sucesso!', 'success')
    except (ValueError, TypeError):
        flash('Dados inválidos.', 'danger')

    mes = request.form.get('mes', date.today().month)
    ano = request.form.get('ano', date.today().year)
    return redirect(url_for('metas.listar', mes=mes, ano=ano))


@metas_bp.route('/excluir/<int:id>', methods=['POST'])
@login_required
def excluir(id):
    meta = MetaService.buscar_por_id(id)
    if meta and meta.id_usuario == session['id_usuario']:
        MetaService.excluir(id)
        flash('Meta removida.', 'success')
    else:
        flash('Meta não encontrada.', 'danger')
    return redirect(url_for('metas.listar'))
