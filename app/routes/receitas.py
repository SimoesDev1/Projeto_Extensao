import io
from flask import Blueprint, render_template, redirect, url_for, request, flash, session, Response, send_file
from app.services.receita_service import ReceitaService
from app.utils import login_required
from datetime import datetime, date

# Imports para PDF
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.units import cm

receitas_bp = Blueprint('receitas', __name__, url_prefix='/receitas')

FORMAS_RECEBIMENTO = ['Dinheiro', 'Pix', 'Cartão de Débito', 'Cartão de Crédito', 'Transferência']

@receitas_bp.route('/')
@login_required
def listar():
    mes = request.args.get('mes', type=int)
    ano = request.args.get('ano', type=int)
    id_usuario = session['id_usuario']

    if mes and ano:
        receitas = ReceitaService.filtrar_por_periodo(id_usuario, mes, ano)
    else:
        receitas = ReceitaService.listar_por_usuario(id_usuario)
        mes = None
        ano = None

    total = ReceitaService.calcular_total(receitas)
    return render_template('receitas/listar.html', receitas=receitas, mes=mes, ano=ano, total=total)

@receitas_bp.route('/nova', methods=['GET', 'POST'])
@login_required
def nova():
    if request.method == 'POST':
        try:
            dados = {
                'valor': float(request.form.get('valor', 0)),
                'data_receita': datetime.strptime(request.form.get('data_receita'), '%Y-%m-%d').date(),
                'forma_recebimento': request.form.get('forma_recebimento'),
                'descricao': request.form.get('descricao') or None,
                'id_usuario': session['id_usuario']
            }
            ReceitaService.criar_receita(dados)
            flash('Receita registrada com sucesso!', 'success')
            return redirect(url_for('receitas.listar'))
        except (ValueError, TypeError):
            flash('Dados inválidos. Verifique o formulário.', 'danger')

    return render_template('receitas/novo.html', hoje=date.today(), formas=FORMAS_RECEBIMENTO)

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
                'descricao': request.form.get('descricao') or None
            }
            ReceitaService.atualizar_receita(id, dados)
            flash('Receita atualizada com sucesso!', 'success')
            return redirect(url_for('receitas.listar'))
        except (ValueError, TypeError):
            flash('Dados inválidos.', 'danger')

    return render_template('receitas/editar.html', receita=receita, formas=FORMAS_RECEBIMENTO)

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


@receitas_bp.route('/exportar')
@login_required
def exportar():
    """Exporta as receitas do período filtrado como arquivo PDF elegante (verde esmeralda)."""
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

    total = ReceitaService.calcular_total(receitas)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        'TitleStyle',
        parent=styles['Heading1'],
        fontSize=18,
        textColor=colors.HexColor("#10b981"), # Cor verde esmeralda para receitas
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
    elements.append(Paragraph("Relatório de Receitas — Doce como Mel", title_style))
    elements.append(Paragraph(f"Usuário: {nome_usuario}  |  Período: {periodo_str}", subtitle_style))
    elements.append(Spacer(1, 0.5*cm))

    # Dados da Tabela
    data = [['Data', 'Descrição', 'Forma de Recebimento', 'Valor (R$)']]
    
    for r in receitas:
        data.append([
            r.data_receita.strftime('%d/%m/%Y'),
            r.descricao or '—',
            r.forma_recebimento,
            f'R$ {r.valor:.2f}'.replace('.', ',')
        ])
    
    # Linha de Total
    data.append(['', '', 'TOTAL', f'R$ {total:.2f}'.replace('.', ',')])

    # Estilização da Tabela (usando paleta verde)
    table = Table(data, colWidths=[3*cm, 7.5*cm, 4*cm, 2.5*cm])
    style = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#069669")), # Verde escuro
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
        ('LINEBELOW', (0, 0), (-1, 0), 1, colors.HexColor("#10b981")),
        ('LINEBELOW', (0, -2), (-1, -2), 1, colors.HexColor("#2d3748")),
        ('GRID', (0, 0), (-1, -2), 0.5, colors.HexColor("#e2e8f0")),
    ])
    table.setStyle(style)
    
    elements.append(table)
    
    # Gera o PDF
    doc.build(elements)
    
    buffer.seek(0)
    
    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype='application/pdf'
    )


@receitas_bp.route('/exportar-csv')
@login_required
def exportar_csv():
    """Exporta as receitas do período filtrado como arquivo CSV."""
    import csv
    from io import StringIO
    
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
    cw.writerow(['Data', 'Descricao', 'Forma de Recebimento', 'Valor'])
    
    for r in receitas:
        cw.writerow([
            r.data_receita.strftime('%d/%m/%Y'),
            r.descricao or '',
            r.forma_recebimento,
            f'{r.valor:.2f}'
        ])

    output = Response(si.getvalue().encode('utf-8-sig'), mimetype='text/csv')
    output.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return output
