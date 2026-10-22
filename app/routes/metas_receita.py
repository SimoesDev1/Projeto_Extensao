"""
Metas de Receita — define metas mensais de faturamento por tipo de receita.
"""
import logging
from datetime import date
from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from app.models.meta_receita import MetaReceita
from app.services.receita_service import ReceitaService
from app import db
from app.utils import login_required

logger = logging.getLogger(__name__)
metas_receita_bp = Blueprint('metas_receita', __name__, url_prefix='/metas-receita')

TIPOS_RECEITA = ['Venda', 'Encomenda', 'Serviço', 'Delivery', 'Feira/Evento', 'Outros']


@metas_receita_bp.route('/')
@login_required
def listar():
    hoje = date.today()
    id_usuario = session['id_usuario']
    mes = request.args.get('mes', type=int, default=hoje.month)
    ano = request.args.get('ano', type=int, default=hoje.year)

    metas = MetaReceita.query.filter_by(id_usuario=id_usuario, mes=mes, ano=ano).all()

    # Calcula progresso de cada meta
    receitas = ReceitaService.filtrar_por_periodo(id_usuario, mes, ano)
    por_tipo = ReceitaService.agrupar_por_tipo(receitas)

    progresso = []
    for meta in metas:
        real = float(por_tipo.get(meta.tipo_receita, 0))
        limite = float(meta.valor_meta)
        pct = round((real / limite) * 100, 1) if limite > 0 else 0
        progresso.append({
            'meta': meta,
            'tipo': meta.tipo_receita,
            'real': real,
            'meta_val': limite,
            'pct': min(pct, 100),
            'atingiu': real >= limite,
        })

    return render_template(
        'metas_receita/listar.html',
        progresso=progresso,
        tipos=TIPOS_RECEITA,
        mes=mes, ano=ano
    )


@metas_receita_bp.route('/salvar', methods=['POST'])
@login_required
def salvar():
    id_usuario = session['id_usuario']
    try:
        tipo = request.form.get('tipo_receita', '').strip()
        mes = int(request.form.get('mes'))
        ano = int(request.form.get('ano'))
        valor = float(request.form.get('valor_meta', 0))

        if not tipo or tipo not in TIPOS_RECEITA:
            flash('Tipo de receita inválido.', 'danger')
        elif valor <= 0:
            flash('O valor da meta deve ser maior que zero.', 'danger')
        else:
            meta = MetaReceita.query.filter_by(
                id_usuario=id_usuario, tipo_receita=tipo, mes=mes, ano=ano
            ).first()
            if meta:
                meta.valor_meta = valor
            else:
                meta = MetaReceita(
                    id_usuario=id_usuario, tipo_receita=tipo,
                    mes=mes, ano=ano, valor_meta=valor
                )
                db.session.add(meta)
            db.session.commit()
            flash('Meta de receita salva com sucesso!', 'success')
    except (ValueError, TypeError):
        flash('Dados inválidos.', 'danger')

    mes = request.form.get('mes', date.today().month)
    ano = request.form.get('ano', date.today().year)
    return redirect(url_for('metas_receita.listar', mes=mes, ano=ano))


@metas_receita_bp.route('/excluir/<int:id>', methods=['POST'])
@login_required
def excluir(id):
    meta = MetaReceita.query.get(id)
    if meta and meta.id_usuario == session['id_usuario']:
        db.session.delete(meta)
        db.session.commit()
        flash('Meta de receita removida.', 'success')
    else:
        flash('Meta não encontrada.', 'danger')
    return redirect(url_for('metas_receita.listar'))
