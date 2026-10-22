"""
Rota do Chat com IA Financeira — endpoint AJAX utilizado pelo widget flutuante.
"""
import logging
from datetime import date
from flask import Blueprint, request, jsonify, session
from app.services.ai_chat_service import AiChatService
from app.services.gasto_service import GastoService
from app.services.receita_service import ReceitaService
from app.utils import login_required

logger = logging.getLogger(__name__)
ia_chat_bp = Blueprint('ia_chat', __name__, url_prefix='/ia-chat')


@ia_chat_bp.route('/perguntar', methods=['POST'])
@login_required
def perguntar():
    """Recebe pergunta via JSON e retorna resposta da IA."""
    data = request.get_json(silent=True) or {}
    pergunta = (data.get('pergunta') or '').strip()

    if not pergunta:
        return jsonify({'erro': 'Pergunta vazia.'}), 400
    if len(pergunta) > 500:
        return jsonify({'erro': 'Pergunta muito longa (máximo 500 caracteres).'}), 400

    id_usuario = session['id_usuario']
    hoje = date.today()
    mes = data.get('mes', hoje.month)
    ano = data.get('ano', hoje.year)

    # Monta contexto financeiro do período atual
    gastos = GastoService.filtrar_por_periodo(id_usuario, mes, ano)
    receitas = ReceitaService.filtrar_por_periodo(id_usuario, mes, ano)
    total_rec = float(ReceitaService.calcular_total(receitas))
    total_desp = float(GastoService.calcular_total(gastos))
    por_cat = {k: float(v) for k, v in GastoService.agrupar_por_categoria(gastos).items()}
    por_tipo = {k: float(v) for k, v in ReceitaService.agrupar_por_tipo(receitas).items()}

    contexto = {
        'total_receitas': total_rec,
        'total_despesas': total_desp,
        'saldo': total_rec - total_desp,
        'por_categoria': por_cat,
        'por_tipo_receita': por_tipo,
        'mes': mes,
        'ano': ano,
        'nome_usuario': session.get('nome_usuario', 'Usuário'),
    }

    resposta = AiChatService.responder(pergunta, contexto)
    return jsonify({'resposta': resposta})


@ia_chat_bp.route('/status')
@login_required
def status():
    """Verifica se a IA está disponível."""
    disponivel = AiChatService.is_configurado()
    return jsonify({'disponivel': disponivel})
