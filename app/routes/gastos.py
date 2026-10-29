import csv
import logging
from io import StringIO
from datetime import datetime, date

from flask import (Blueprint, render_template, redirect, url_for,
                   request, flash, session, Response, send_file, jsonify)

from app.services.gasto_service import GastoService
from app.services.categoria_service import CategoriaService
from app.services.pdf_service import PdfService
from app.utils import login_required

logger = logging.getLogger(__name__)
gastos_bp = Blueprint('gastos', __name__, url_prefix='/gastos')

FORMAS_PAGAMENTO = ['Dinheiro', 'Pix', 'Cartão de Débito', 'Cartão de Crédito']
PER_PAGE = 20


@gastos_bp.route('/')
@login_required
def listar():
    id_usuario = session['id_usuario']
    mes = request.args.get('mes', type=int)
    ano = request.args.get('ano', type=int)
    busca = request.args.get('busca', '').strip() or None
    id_categoria = request.args.get('id_categoria', type=int) or None
    parcelados = request.args.get('parcelados', type=int) == 1
    page = request.args.get('page', type=int, default=1)

    categorias = CategoriaService.listar_todas()

    paginacao = GastoService.listar_paginado(
        id_usuario=id_usuario, page=page, per_page=PER_PAGE,
        mes=mes, ano=ano, busca=busca, id_categoria=id_categoria,
        apenas_parcelados=parcelados
    )

    gastos = paginacao.items
    total = GastoService.calcular_total(gastos)
    projecoes = GastoService.buscar_projecao_parcelas_futuras(id_usuario, 4)

    return render_template(
        'gastos/listar.html',
        gastos=gastos, paginacao=paginacao,
        mes=mes, ano=ano, busca=busca or '',
        id_categoria=id_categoria, categorias=categorias,
        parcelados=parcelados, projecoes=projecoes,
        total=float(total)
    )


@gastos_bp.route('/novo', methods=['GET', 'POST'])
@login_required
def novo():
    categorias = CategoriaService.listar_todas()

    if request.method == 'POST':
        try:
            dados = {
                'valor': float(request.form.get('valor', 0)),
                'data_gasto': datetime.strptime(request.form.get('data_gasto'), '%Y-%m-%d').date(),
                'forma_pagamento': request.form.get('forma_pagamento'),
                'descricao': request.form.get('descricao') or None,
                'id_usuario': session['id_usuario'],
                'id_categoria': int(request.form.get('id_categoria', 0)),
                'is_pago': request.form.get('is_pago') == 'on'
            }
            if dados['valor'] <= 0:
                flash('O valor da despesa deve ser maior que zero.', 'danger')
                return render_template('gastos/novo.html', categorias=categorias, hoje=date.today(),
                                       formas=FORMAS_PAGAMENTO)
            if dados['id_categoria'] <= 0:
                flash('Selecione uma categoria válida.', 'danger')
                return render_template('gastos/novo.html', categorias=categorias, hoje=date.today(),
                                       formas=FORMAS_PAGAMENTO)

            is_parcelado = request.form.get('is_parcelado') in ['on', '1', 'true', 'True']
            try:
                numero_parcelas = int(request.form.get('numero_parcelas', 1))
            except (ValueError, TypeError):
                numero_parcelas = 1

            if not is_parcelado or numero_parcelas < 1:
                numero_parcelas = 1

            # Upload opcional de comprovante
            if 'comprovante' in request.files:
                file = request.files['comprovante']
                if file and file.filename != '':
                    from app.services.comprovante_service import salvar_e_otimizar_comprovante
                    comprovante_salvo = salvar_e_otimizar_comprovante(file)
                    if comprovante_salvo:
                        dados['comprovante'] = comprovante_salvo

        except (ValueError, TypeError):
            flash('Dados inválidos. Verifique o formulário.', 'danger')
            return render_template('gastos/novo.html', categorias=categorias, hoje=date.today(),
                                   formas=FORMAS_PAGAMENTO)

        if numero_parcelas > 1:
            GastoService.criar_gasto_parcelado(dados, numero_parcelas)
            logger.info(f"Compra parcelada criada por usuário {session['id_usuario']}: {numero_parcelas}x de R${dados['valor']/numero_parcelas:.2f}")
            flash(f'Compra parcelada em {numero_parcelas}x com sucesso! {numero_parcelas} lançamentos gerados nos respectivos meses.', 'success')
        else:
            GastoService.criar_gasto(dados)
            logger.info(f"Gasto criado por usuário {session['id_usuario']}: R${dados['valor']}")
            flash('Despesa cadastrada com sucesso!', 'success')

        return redirect(url_for('gastos.listar'))

    return render_template('gastos/novo.html', categorias=categorias, hoje=date.today(),
                           formas=FORMAS_PAGAMENTO)


@gastos_bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar(id):
    gasto = GastoService.buscar_por_id(id)
    if not gasto or gasto.id_usuario != session['id_usuario']:
        flash('Despesa não encontrada.', 'danger')
        return redirect(url_for('gastos.listar'))

    categorias = CategoriaService.listar_todas()

    if request.method == 'POST':
        try:
            dados = {
                'valor': float(request.form.get('valor', 0)),
                'data_gasto': datetime.strptime(request.form.get('data_gasto'), '%Y-%m-%d').date(),
                'forma_pagamento': request.form.get('forma_pagamento'),
                'descricao': request.form.get('descricao') or None,
                'id_categoria': int(request.form.get('id_categoria', 0)),
                'is_pago': request.form.get('is_pago') == 'on'
            }
            if dados['valor'] <= 0:
                flash('O valor da despesa deve ser maior que zero.', 'danger')
                return render_template('gastos/editar.html', gasto=gasto, categorias=categorias,
                                       formas=FORMAS_PAGAMENTO)
            if dados['id_categoria'] <= 0:
                flash('Selecione uma categoria válida.', 'danger')
                return render_template('gastos/editar.html', gasto=gasto, categorias=categorias,
                                       formas=FORMAS_PAGAMENTO)
        except (ValueError, TypeError):
            flash('Dados inválidos. Verifique o formulário.', 'danger')
            return render_template('gastos/editar.html', gasto=gasto, categorias=categorias,
                                   formas=FORMAS_PAGAMENTO)

        GastoService.atualizar_gasto(id, dados)
        flash('Despesa atualizada com sucesso!', 'success')
        return redirect(url_for('gastos.listar'))

    return render_template('gastos/editar.html', gasto=gasto, categorias=categorias,
                           formas=FORMAS_PAGAMENTO)


@gastos_bp.route('/excluir/<int:id>', methods=['POST'])
@login_required
def excluir(id):
    gasto = GastoService.buscar_por_id(id)
    if gasto and gasto.id_usuario == session['id_usuario']:
        excluir_todas = request.form.get('excluir_todas') == '1'
        qtd = GastoService.excluir_gasto(id, excluir_todas_parcelas=excluir_todas)
        if excluir_todas and qtd > 1:
            flash(f'Todas as {qtd} parcelas desta compra foram excluídas!', 'success')
        else:
            flash('Despesa excluída com sucesso!', 'success')
    else:
        flash('Despesa não encontrada.', 'danger')
    return redirect(url_for('gastos.listar'))



@gastos_bp.route('/detalhes/<int:id>')
@login_required
def detalhes(id):
    """Exibe a visualização detalhada de uma despesa, incluindo comprovante da IA e parcelas."""
    gasto = GastoService.buscar_por_id(id)
    if not gasto or gasto.id_usuario != session['id_usuario']:
        flash('Despesa não encontrada.', 'danger')
        return redirect(url_for('gastos.listar'))

    parcelas_relacionadas = []
    if gasto.id_parcelamento:
        from app.models.gasto import Gasto
        parcelas_relacionadas = (Gasto.query
                                .filter_by(id_parcelamento=gasto.id_parcelamento, id_usuario=gasto.id_usuario)
                                .order_by(Gasto.parcela_atual.asc())
                                .all())

    is_pdf = bool(gasto.comprovante and gasto.comprovante.lower().endswith('.pdf'))

    return render_template(
        'gastos/detalhes.html',
        gasto=gasto,
        parcelas_relacionadas=parcelas_relacionadas,
        is_pdf=is_pdf
    )


@gastos_bp.route('/exportar')
@login_required
def exportar():
    """Exporta os gastos do período filtrado como PDF via PdfService centralizado."""
    mes = request.args.get('mes', type=int)
    ano = request.args.get('ano', type=int)
    id_usuario = session['id_usuario']
    nome_usuario = session.get('nome_usuario', 'Usuário')

    if not mes or not ano:
        gastos = GastoService.listar_por_usuario(id_usuario)
        periodo_str = "Todos os Registros"
        filename = f'relatorio_gastos_completo_{date.today()}.pdf'
    else:
        gastos = GastoService.filtrar_por_periodo(id_usuario, mes, ano)
        periodo_str = f"{mes:02d}/{ano}"
        filename = f'relatorio_gastos_{mes:02d}_{ano}.pdf'

    buffer = PdfService.gerar_relatorio_gastos(gastos, periodo_str, nome_usuario)

    return send_file(buffer, as_attachment=True, download_name=filename,
                     mimetype='application/pdf')


@gastos_bp.route('/exportar-csv')
@login_required
def exportar_csv():
    """Exporta os gastos do período filtrado como CSV."""
    mes = request.args.get('mes', type=int)
    ano = request.args.get('ano', type=int)
    id_usuario = session['id_usuario']

    if not mes or not ano:
        gastos = GastoService.listar_por_usuario(id_usuario)
        filename = f'todas_despesas_{date.today()}.csv'
    else:
        gastos = GastoService.filtrar_por_periodo(id_usuario, mes, ano)
        filename = f'despesas_{mes:02d}_{ano}.csv'

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['Data', 'Descricao', 'Categoria', 'Forma de Pagamento', 'Valor', 'Pago'])

    for g in gastos:
        cw.writerow([
            g.data_gasto.strftime('%d/%m/%Y'),
            g.descricao or '',
            g.categoria.nome_categoria,
            g.forma_pagamento,
            f'{float(g.valor):.2f}',
            'Sim' if g.is_pago else 'Não'
        ])

    output = Response(si.getvalue().encode('utf-8-sig'), mimetype='text/csv')
    output.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return output


@gastos_bp.route('/<int:id>/pago', methods=['POST'])
@login_required
def marcar_como_pago(id):
    gasto = GastoService.buscar_por_id(id)
    if not gasto or gasto.id_usuario != session['id_usuario']:
        return jsonify({'error': 'Despesa não encontrada.'}), 404

    gasto.is_pago = not gasto.is_pago
    GastoService.atualizar_gasto(id, {'is_pago': gasto.is_pago})
    logger.info(f"Status de pagamento da despesa {id} invertido por usuário {session['id_usuario']}: {gasto.is_pago}")

    return jsonify({'pago': gasto.is_pago})
