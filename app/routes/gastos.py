import io
from flask import Blueprint, render_template, redirect, url_for, request, flash, session, Response, send_file
from app.services.gasto_service import GastoService
from app.services.categoria_service import CategoriaService
from app.utils import login_required
from datetime import datetime, date

# Imports para PDF
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.units import cm

gastos_bp = Blueprint('gastos', __name__, url_prefix='/gastos')

FORMAS_PAGAMENTO = ['Dinheiro', 'Pix', 'Cartão de Débito', 'Cartão de Crédito']


@gastos_bp.route('/')
@login_required
def listar():
    mes = request.args.get('mes', type=int)
    ano = request.args.get('ano', type=int)
    id_usuario = session['id_usuario']

    if mes and ano:
        gastos = GastoService.filtrar_por_periodo(id_usuario, mes, ano)
    else:
        gastos = GastoService.listar_por_usuario(id_usuario)
        mes = None
        ano = None

    total = GastoService.calcular_total(gastos)
    return render_template('gastos/listar.html', gastos=gastos, mes=mes, ano=ano, total=total)


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
                'id_categoria': int(request.form.get('id_categoria', 0))
            }
        except (ValueError, TypeError):
            flash('Dados inválidos. Verifique o formulário.', 'danger')
            return render_template('gastos/novo.html', categorias=categorias, hoje=date.today(),
                                   formas=FORMAS_PAGAMENTO)

        GastoService.criar_gasto(dados)
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
                'id_categoria': int(request.form.get('id_categoria', 0))
            }
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
        GastoService.excluir_gasto(id)
        flash('Despesa excluída com sucesso!', 'success')
    else:
        flash('Despesa não encontrada.', 'danger')
    return redirect(url_for('gastos.listar'))


@gastos_bp.route('/exportar')
@login_required
def exportar():
    """Exporta os gastos do período filtrado como arquivo PDF elegante."""
    mes = request.args.get('mes', type=int)
    ano = request.args.get('ano', type=int)
    id_usuario = session['id_usuario']
    nome_usuario = session.get('nome_usuario', 'Usuário')

    if not mes or not ano:
        flash('Selecione um mês e ano para exportar o relatório.', 'danger')
        return redirect(url_for('gastos.listar'))

    gastos = GastoService.filtrar_por_periodo(id_usuario, mes, ano)
    total = GastoService.calcular_total(gastos)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    
    styles = getSampleStyleSheet()
    
    # Estilo customizado para título e subtítulo
    title_style = ParagraphStyle(
        'TitleStyle',
        parent=styles['Heading1'],
        fontSize=18,
        textColor=colors.HexColor("#f59e0b"), # Cor âmbar do projeto
        spaceAfter=12,
        alignment=1 # Center
    )
    
    subtitle_style = ParagraphStyle(
        'SubtitleStyle',
        parent=styles['Normal'],
        fontSize=12,
        textColor=colors.HexColor("#718096"),
        spaceAfter=20,
        alignment=1 # Center
    )

    elements = []

    # Cabeçalho do PDF
    elements.append(Paragraph("Relatório de Despesas — Doce como Mel", title_style))
    elements.append(Paragraph(f"Usuário: {nome_usuario}  |  Período: {mes:02d}/{ano}", subtitle_style))
    elements.append(Spacer(1, 0.5*cm))

    # Dados da Tabela
    data = [['Data', 'Descrição', 'Categoria', 'Forma de Pagamento', 'Valor (R$)']]
    
    for g in gastos:
        data.append([
            g.data_gasto.strftime('%d/%m/%Y'),
            g.descricao or '—',
            g.categoria.nome_categoria,
            g.forma_pagamento,
            f'R$ {g.valor:.2f}'.replace('.', ',')
        ])
    
    # Linha de Total
    data.append(['', '', '', 'TOTAL', f'R$ {total:.2f}'.replace('.', ',')])

    # Estilização da Tabela
    table = Table(data, colWidths=[2.5*cm, 5*cm, 3.5*cm, 3.5*cm, 2.5*cm])
    style = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1c2333")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('BACKGROUND', (0, 1), (-1, -2), colors.white),
        ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
        ('ALIGN', (-1, 0), (-1, -1), 'RIGHT'), # Alinha valor à direita
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('TOPPADDING', (0, -1), (-1, -1), 10),
        ('LINEBELOW', (0, 0), (-1, 0), 1, colors.HexColor("#f59e0b")),
        ('LINEBELOW', (0, -2), (-1, -2), 1, colors.HexColor("#2d3748")),
        ('GRID', (0, 0), (-1, -2), 0.5, colors.HexColor("#e2e8f0")),
    ])
    table.setStyle(style)
    
    elements.append(table)
    
    # Gera o PDF
    doc.build(elements)
    
    buffer.seek(0)
    filename = f'relatorio_gastos_{mes:02d}_{ano}.pdf'
    
    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype='application/pdf'
    )


@gastos_bp.route('/exportar-csv')
@login_required
def exportar_csv():
    """Exporta os gastos do período filtrado como arquivo CSV."""
    import csv
    from io import StringIO
    
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
    cw.writerow(['Data', 'Descricao', 'Categoria', 'Forma de Pagamento', 'Valor'])
    
    for g in gastos:
        cw.writerow([
            g.data_gasto.strftime('%d/%m/%Y'),
            g.descricao or '',
            g.categoria.nome_categoria,
            g.forma_pagamento,
            f'{g.valor:.2f}'
        ])

    output = Response(si.getvalue().encode('utf-8-sig'), mimetype='text/csv')
    output.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return output
