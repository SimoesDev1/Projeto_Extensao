from flask import Blueprint, render_template, request, session, jsonify
from app.services.gasto_service import GastoService
from app.services.receita_service import ReceitaService
from app.utils import login_required
from datetime import date

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')


@dashboard_bp.route('/')
@login_required
def index():
    hoje = date.today()
    id_usuario = session['id_usuario']

    # Permite filtrar por mês/ano via query string; padrão = mês atual
    mes = request.args.get('mes', type=int, default=hoje.month)
    ano = request.args.get('ano', type=int, default=hoje.year)

    gastos = GastoService.filtrar_por_periodo(id_usuario, mes, ano)
    total_gastos = GastoService.calcular_total(gastos)
    por_categoria = GastoService.agrupar_por_categoria(gastos)
    # Converte Decimal para float para o JSON no template
    por_categoria_float = {k: float(v) for k, v in por_categoria.items()}

    receitas = ReceitaService.filtrar_por_periodo(id_usuario, mes, ano)
    total_receitas = ReceitaService.calcular_total(receitas)
    por_tipo_receita = ReceitaService.agrupar_por_tipo(receitas)
    por_tipo_receita_float = {k: float(v) for k, v in por_tipo_receita.items()}

    saldo_real = total_receitas - total_gastos

    # Cálculo do mês anterior para comparação (gastos)
    mes_ant = 12 if mes == 1 else mes - 1
    ano_ant = ano - 1 if mes == 1 else ano
    total_ant_gastos = GastoService.calcular_total_periodo_sql(id_usuario, mes_ant, ano_ant)

    # Percentual de variação (gastos)
    alertas = GastoService.detectar_anomalias(id_usuario, mes, ano)
    return render_template(
        'dashboard/index.html',
        total_mes=float(total_gastos),
        total_receitas=float(total_receitas),
        saldo_real=float(saldo_real),
        total_ant=float(total_ant_gastos),
        variacao=variacao_gastos,
        por_categoria=por_categoria_float,
        por_tipo_receita=por_tipo_receita_float,
        mes=mes,
        ano=ano,
        num_gastos=len(gastos),
        num_receitas=len(receitas),
        alertas=alertas,
    )


@dashboard_bp.route('/api/evolucao')
@login_required
def evolucao_json():
    """API endpoint que retorna dados de evolução mensal para o gráfico de linha."""
    id_usuario = session['id_usuario']
    num_meses = request.args.get('meses', type=int, default=6)
    gastos_ev = GastoService.evolucao_mensal(id_usuario, num_meses)
    receitas_ev = ReceitaService.evolucao_mensal(id_usuario, num_meses)

    meses_nomes = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun',
                   'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
    labels = [f"{meses_nomes[d['mes']-1]}/{str(d['ano'])[2:]}" for d in gastos_ev]

    return jsonify({
        'labels': labels,
        'gastos': [d['total'] for d in gastos_ev],
        'receitas': [d['total'] for d in receitas_ev],
    })
