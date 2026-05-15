from flask import Blueprint, render_template, request, session
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

    receitas = ReceitaService.filtrar_por_periodo(id_usuario, mes, ano)
    total_receitas = ReceitaService.calcular_total(receitas)

    saldo_real = total_receitas - total_gastos

    # Cálculo do mês anterior para comparação (gastos)
    mes_ant = 12 if mes == 1 else mes - 1
    ano_ant = ano - 1 if mes == 1 else ano
    gastos_ant = GastoService.filtrar_por_periodo(id_usuario, mes_ant, ano_ant)
    total_ant_gastos = GastoService.calcular_total(gastos_ant)

    # Percentual de variação (gastos)
    variacao_gastos = 0
    if total_ant_gastos > 0:
        variacao_gastos = ((total_gastos - total_ant_gastos) / total_ant_gastos) * 100

    return render_template(
        'dashboard/index.html',
        total_mes=total_gastos,
        total_receitas=total_receitas,
        saldo_real=saldo_real,
        total_ant=total_ant_gastos,
        variacao=variacao_gastos,
        por_categoria=por_categoria,
        mes=mes,
        ano=ano,
        num_gastos=len(gastos)
    )
