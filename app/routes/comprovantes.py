"""
Galeria de Comprovantes — histórico visual de todos os arquivos anexados.
"""
import os
import logging
from datetime import date
from flask import Blueprint, render_template, session, request
from app.services.gasto_service import GastoService
from app.services.receita_service import ReceitaService
from app.utils import login_required

logger = logging.getLogger(__name__)
comprovantes_bp = Blueprint('comprovantes', __name__, url_prefix='/comprovantes-galeria')


@comprovantes_bp.route('/')
@login_required
def galeria():
    """Galeria de todos os comprovantes anexados (despesas + receitas)."""
    id_usuario = session['id_usuario']
    mes = request.args.get('mes', type=int)
    ano = request.args.get('ano', type=int)
    hoje = date.today()

    # Busca gastos e receitas com comprovante
    if mes and ano:
        gastos = GastoService.filtrar_por_periodo(id_usuario, mes, ano)
        receitas = ReceitaService.filtrar_por_periodo(id_usuario, mes, ano)
    else:
        gastos = GastoService.listar_por_usuario(id_usuario)
        receitas = ReceitaService.listar_por_usuario(id_usuario)

    # Filtra apenas os que têm comprovante
    itens = []
    for g in gastos:
        if g.comprovante:
            ext = g.comprovante.rsplit('.', 1)[-1].lower() if '.' in g.comprovante else ''
            itens.append({
                'tipo': 'despesa',
                'id': g.id_gasto,
                'data': g.data_gasto,
                'descricao': g.descricao or g.categoria.nome_categoria,
                'valor': float(g.valor),
                'comprovante': g.comprovante,
                'is_pdf': ext == 'pdf',
                'url_detalhe': f'/gastos/detalhes/{g.id_gasto}',
            })
    for r in receitas:
        if r.comprovante:
            ext = r.comprovante.rsplit('.', 1)[-1].lower() if '.' in r.comprovante else ''
            itens.append({
                'tipo': 'receita',
                'id': r.id_receita,
                'data': r.data_receita,
                'descricao': r.descricao or r.tipo_receita or 'Receita',
                'valor': float(r.valor),
                'comprovante': r.comprovante,
                'is_pdf': ext == 'pdf',
                'url_detalhe': f'/receitas/detalhes/{r.id_receita}',
            })

    # Ordena por data decrescente
    itens.sort(key=lambda x: x['data'], reverse=True)

    return render_template(
        'comprovantes/galeria.html',
        itens=itens,
        mes=mes, ano=ano,
        mes_atual=hoje.month, ano_atual=hoje.year,
        total=len(itens)
    )
