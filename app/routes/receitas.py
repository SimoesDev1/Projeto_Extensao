import csv
import logging
from io import StringIO
from datetime import datetime, date

from flask import (Blueprint, render_template, redirect, url_for,
                   request, flash, session, Response, send_file)

from app.services.receita_service import ReceitaService
from app.services.pdf_service import PdfService
from app.utils import login_required

logger = logging.getLogger(__name__)
receitas_bp = Blueprint('receitas', __name__, url_prefix='/receitas')

FORMAS_RECEBIMENTO = ['Dinheiro', 'Pix', 'Cartão de Débito', 'Cartão de Crédito', 'Transferência']
TIPOS_RECEITA = ['Venda', 'Encomenda', 'Serviço', 'Delivery', 'Feira/Evento', 'Outros']
PER_PAGE = 20


@receitas_bp.route('/')
@login_required
def listar():
    id_usuario = session['id_usuario']
    mes = request.args.get('mes', type=int)
    ano = request.args.get('ano', type=int)
    busca = request.args.get('busca', '').strip() or None
    page = request.args.get('page', type=int, default=1)

    paginacao = ReceitaService.listar_paginado(
        id_usuario=id_usuario, page=page, per_page=PER_PAGE,
        mes=mes, ano=ano, busca=busca
    )

    receitas = paginacao.items
    total = ReceitaService.calcular_total(receitas)

    return render_template(
        'receitas/listar.html',
        receitas=receitas, paginacao=paginacao,
        mes=mes, ano=ano, busca=busca or '',
        total=float(total)
    )


@receitas_bp.route('/nova', methods=['GET', 'POST'])
@login_required
def nova():
    if request.method == 'POST':
        try:
            dados = {
                'valor': float(request.form.get('valor', 0)),
                'data_receita': datetime.strptime(request.form.get('data_receita'), '%Y-%m-%d').date(),
                'forma_recebimento': request.form.get('forma_recebimento'),
                'tipo_receita': request.form.get('tipo_receita', 'Venda'),
                'descricao': request.form.get('descricao') or None,
                'id_usuario': session['id_usuario']
            }
            if dados['valor'] <= 0:
                flash('O valor da receita deve ser maior que zero.', 'danger')
                return render_template('receitas/novo.html', hoje=date.today(),
                                       formas=FORMAS_RECEBIMENTO, tipos=TIPOS_RECEITA)

            # Upload opcional de comprovante
            if 'comprovante' in request.files:
                file = request.files['comprovante']
                if file and file.filename != '':
                    import os, uuid
                    from flask import current_app
                    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
                    if ext in ['png', 'jpg', 'jpeg', 'webp', 'pdf']:
                        comprovante_nome = f"{uuid.uuid4()}.{ext}"
                        caminho = os.path.join(current_app.config['UPLOAD_COMPROVANTES_DIR'], comprovante_nome)
                        file.save(caminho)
                        dados['comprovante'] = comprovante_nome

            ReceitaService.criar_receita(dados)
            logger.info(f"Receita criada por usuário {session['id_usuario']}: R${dados['valor']}")
            flash('Receita registrada com sucesso!', 'success')
            return redirect(url_for('receitas.listar'))
        except (ValueError, TypeError):
            flash('Dados inválidos. Verifique o formulário.', 'danger')

    return render_template('receitas/novo.html', hoje=date.today(),
                           formas=FORMAS_RECEBIMENTO, tipos=TIPOS_RECEITA)


@receitas_bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar(id):
    receita = ReceitaService.buscar_por_id(id)
    if not receita or receita.id_usuario != session['id_usuario']:
        flash('Receita não encontrada.', 'danger')
        return redirect(url_for('receitas.listar'))

    if request.method == 'POST':
        try:
            dados = {
                'valor': float(request.form.get('valor', 0)),
                'data_receita': datetime.strptime(request.form.get('data_receita'), '%Y-%m-%d').date(),
                'forma_recebimento': request.form.get('forma_recebimento'),
                'tipo_receita': request.form.get('tipo_receita', receita.tipo_receita),
                'descricao': request.form.get('descricao') or None
            }
            if dados['valor'] <= 0:
                flash('O valor da receita deve ser maior que zero.', 'danger')
                return render_template('receitas/editar.html', receita=receita,
                                       formas=FORMAS_RECEBIMENTO, tipos=TIPOS_RECEITA)
            ReceitaService.atualizar_receita(id, dados)
            flash('Receita atualizada com sucesso!', 'success')
            return redirect(url_for('receitas.listar'))
        except (ValueError, TypeError):
            flash('Dados inválidos.', 'danger')

    return render_template('receitas/editar.html', receita=receita,
                           formas=FORMAS_RECEBIMENTO, tipos=TIPOS_RECEITA)


@receitas_bp.route('/excluir/<int:id>', methods=['POST'])
@login_required
def excluir(id):
    receita = ReceitaService.buscar_por_id(id)
    if receita and receita.id_usuario == session['id_usuario']:
        ReceitaService.excluir_receita(id)
        flash('Receita excluída com sucesso!', 'success')
    else:
        flash('Erro ao excluir.', 'danger')
    return redirect(url_for('receitas.listar'))


@receitas_bp.route('/detalhes/<int:id>')
@login_required
def detalhes(id):
    """Exibe a visualização detalhada de uma receita, incluindo comprovante da IA."""
    receita = ReceitaService.buscar_por_id(id)
    if not receita or receita.id_usuario != session['id_usuario']:
        flash('Receita não encontrada.', 'danger')
        return redirect(url_for('receitas.listar'))

    is_pdf = bool(receita.comprovante and receita.comprovante.lower().endswith('.pdf'))

    return render_template(
        'receitas/detalhes.html',
        receita=receita,
        is_pdf=is_pdf
    )


@receitas_bp.route('/exportar')
@login_required
def exportar():
    """Exporta as receitas como PDF via PdfService centralizado."""
    mes = request.args.get('mes', type=int)
    ano = request.args.get('ano', type=int)
    id_usuario = session['id_usuario']
    nome_usuario = session.get('nome_usuario', 'Usuário')

    if not mes or not ano:
        receitas = ReceitaService.listar_por_usuario(id_usuario)
        periodo_str = "Todos os Registros"
        filename = f'relatorio_receitas_completo_{date.today()}.pdf'
    else:
        receitas = ReceitaService.filtrar_por_periodo(id_usuario, mes, ano)
        periodo_str = f"{mes:02d}/{ano}"
        filename = f'relatorio_receitas_{mes:02d}_{ano}.pdf'

    buffer = PdfService.gerar_relatorio_receitas(receitas, periodo_str, nome_usuario)

    return send_file(buffer, as_attachment=True, download_name=filename,
                     mimetype='application/pdf')


@receitas_bp.route('/exportar-csv')
@login_required
def exportar_csv():
    """Exporta as receitas como CSV."""
    mes = request.args.get('mes', type=int)
    ano = request.args.get('ano', type=int)
    id_usuario = session['id_usuario']

    if not mes or not ano:
        receitas = ReceitaService.listar_por_usuario(id_usuario)
        filename = f'todas_receitas_{date.today()}.csv'
    else:
        receitas = ReceitaService.filtrar_por_periodo(id_usuario, mes, ano)
        filename = f'receitas_{mes:02d}_{ano}.csv'

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['Data', 'Descricao', 'Tipo', 'Forma de Recebimento', 'Valor'])

    for r in receitas:
        cw.writerow([
            r.data_receita.strftime('%d/%m/%Y'),
            r.descricao or '',
            r.tipo_receita or '',
            r.forma_recebimento,
            f'{float(r.valor):.2f}'
        ])

    output = Response(si.getvalue().encode('utf-8-sig'), mimetype='text/csv')
    output.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return output
