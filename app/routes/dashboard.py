from flask import Blueprint, render_template, request, session
from app.services.gasto_service import GastoService
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
    total_mes = GastoService.calcular_total(gastos)
    por_categoria = GastoService.agrupar_por_categoria(gastos)

    return render_template(
        'dashboard/index.html',
        total_mes=total_mes,
        por_categoria=por_categoria,
        mes=mes,
        ano=ano,
        num_gastos=len(gastos)
    )
