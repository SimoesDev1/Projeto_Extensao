from datetime import date
from flask import Blueprint, render_template, request, session, send_file, jsonify
from app.services.gasto_service import GastoService
from app.services.receita_service import ReceitaService
from app.services.pdf_service import PdfService
from app.utils import login_required

relatorios_bp = Blueprint('relatorios', __name__, url_prefix='/relatorios')

MESES_NOMES = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun',
               'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']


@relatorios_bp.route('/')
@login_required
def index():
    hoje = date.today()
    return render_template('relatorios/index.html',
                           mes_atual=hoje.month, ano_atual=hoje.year)


@relatorios_bp.route('/api/dados')
@login_required
def api_dados():
    """Retorna JSON com KPIs e dados de gráficos para o período selecionado."""
    id_usuario = session['id_usuario']
    hoje = date.today()
    mes = request.args.get('mes', type=int, default=hoje.month)
    ano = request.args.get('ano', type=int, default=hoje.year)

    # KPIs do período
    gastos = GastoService.filtrar_por_periodo(id_usuario, mes, ano)
    receitas = ReceitaService.filtrar_por_periodo(id_usuario, mes, ano)

    total_despesas = float(GastoService.calcular_total(gastos))
    total_receitas = float(ReceitaService.calcular_total(receitas))
    saldo = total_receitas - total_despesas
    taxa_economia = round((saldo / total_receitas * 100), 1) if total_receitas > 0 else 0.0

    # Categorias de despesas
    por_categoria = GastoService.agrupar_por_categoria(gastos)
    categorias_labels = list(por_categoria.keys())
    categorias_values = [float(v) for v in por_categoria.values()]

    # Tipos de receita
    por_tipo = ReceitaService.agrupar_por_tipo(receitas)
    tipos_labels = list(por_tipo.keys())
    tipos_values = [float(v) for v in por_tipo.values()]

    # Formas de pagamento (despesas)
    formas_pag = {}
    for g in gastos:
        fp = g.forma_pagamento or 'Outros'
        formas_pag[fp] = formas_pag.get(fp, 0.0) + float(g.valor)
    formas_labels = list(formas_pag.keys())
    formas_values = list(formas_pag.values())

    # Evolução mensal (6 meses) a partir do mês/ano selecionado
    ev_gastos = []
    ev_receitas = []
    ev_labels = []
    for i in range(5, -1, -1):
        m = mes - i
        a = ano
        while m <= 0:
            m += 12
            a -= 1
        ev_labels.append(f"{MESES_NOMES[m-1]}/{str(a)[2:]}")
        ev_gastos.append(float(GastoService.calcular_total_periodo_sql(id_usuario, m, a)))
        ev_receitas.append(float(ReceitaService.calcular_total_periodo_sql(id_usuario, m, a)))

    return jsonify({
        'kpis': {
            'total_receitas': total_receitas,
            'total_despesas': total_despesas,
            'saldo': saldo,
            'taxa_economia': taxa_economia,
            'num_receitas': len(receitas),
            'num_despesas': len(gastos),
        },
        'categorias': {'labels': categorias_labels, 'values': categorias_values},
        'tipos_receita': {'labels': tipos_labels, 'values': tipos_values},
        'formas_pagamento': {'labels': formas_labels, 'values': formas_values},
        'evolucao': {
            'labels': ev_labels,
            'gastos': ev_gastos,
            'receitas': ev_receitas,
        }
    })


@relatorios_bp.route('/consolidado')
@login_required
def consolidado():
    """Gera PDF consolidado com receitas + despesas + balanço."""
    id_usuario = session['id_usuario']
    nome_usuario = session.get('nome_usuario', 'Usuário')
    mes = request.args.get('mes', type=int)
    ano = request.args.get('ano', type=int)

    if not mes or not ano:
        hoje = date.today()
        mes, ano = hoje.month, hoje.year

    gastos = GastoService.filtrar_por_periodo(id_usuario, mes, ano)
    receitas = ReceitaService.filtrar_por_periodo(id_usuario, mes, ano)
    periodo_str = f"{mes:02d}/{ano}"
    filename = f'relatorio_consolidado_{mes:02d}_{ano}.pdf'

    buffer = PdfService.gerar_relatorio_consolidado(gastos, receitas, periodo_str, nome_usuario)

    return send_file(buffer, as_attachment=True, download_name=filename,
                     mimetype='application/pdf')
