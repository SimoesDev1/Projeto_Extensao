"""
Serviço centralizado de geração de PDFs.
Elimina duplicação de código entre gastos.py e receitas.py e expõe utilitários
reutilizáveis por outros serviços (brl, esc, NumberedCanvas).
"""
import io
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from xml.sax.saxutils import escape as _xml_escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


# Nome exibido no título/rodapé. Pode ser sobrescrito por chamada (parâmetro `estabelecimento`).
NOME_ESTABELECIMENTO = "Doce como Mel"

# Paleta de cores do projeto
COR_AMBER = colors.HexColor("#f59e0b")
COR_GREEN = colors.HexColor("#10b981")
COR_GREEN_ESCURO = colors.HexColor("#069669")
COR_DARK = colors.HexColor("#1c2333")
COR_MUTED = colors.HexColor("#718096")
COR_BORDER = colors.HexColor("#e2e8f0")
COR_RED = colors.HexColor("#ef4444")
COR_ZEBRA = colors.HexColor("#f8fafc")
COR_TOTAL_BG = colors.HexColor("#f1f5f9")

# A4 (21 cm) menos as margens laterais de 2 cm
LARGURA_UTIL = 17 * cm


# ----------------------------------------------------------------------
# Utilitários reutilizáveis
# ----------------------------------------------------------------------
def _sanear(texto) -> str:
    """Troca por '?' o que a fonte padrão (Helvetica/cp1252) não consegue desenhar."""
    s = '' if texto is None else str(texto)
    return s.encode('cp1252', errors='replace').decode('cp1252')


def esc(texto) -> str:
    """Prepara texto para uso em Paragraph: sanitiza caracteres e escapa &, < e >."""
    return _xml_escape(_sanear(texto))


def to_decimal(valor) -> Decimal:
    """Converte número/str/Decimal/None em Decimal sem passar por erro de float."""
    if valor is None:
        return Decimal('0')
    if isinstance(valor, Decimal):
        return valor
    return Decimal(str(valor))


def brl(valor) -> str:
    """Formata em reais: 1250.5 -> 'R$ 1.250,50'; -150 -> '-R$ 150,00'."""
    d = to_decimal(valor).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    sinal = '-' if d < 0 else ''
    inteiro, centavos = f"{abs(d):.2f}".split('.')
    return f"{sinal}R$ {int(inteiro):,}".replace(',', '.') + f",{centavos}"


def _fmt_data(valor) -> str:
    try:
        return valor.strftime('%d/%m/%Y')
    except AttributeError:
        return '—'


def _somar(itens) -> Decimal:
    return sum((to_decimal(i.valor) for i in itens), Decimal('0'))


class NumberedCanvas(canvas.Canvas):
    """Canvas com rodapé 'Página X de Y'. O texto da esquerda vem de `_texto_rodape`."""
    _texto_rodape = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total_paginas = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._desenhar_rodape(total_paginas)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

    def _desenhar_rodape(self, total_paginas: int):
        largura = self._pagesize[0]
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(COR_MUTED)
        self.setStrokeColor(COR_BORDER)
        self.setLineWidth(0.5)
        self.line(2 * cm, 1.2 * cm, largura - 2 * cm, 1.2 * cm)
        self.drawString(2 * cm, 0.8 * cm, _sanear(self._texto_rodape))
        self.drawRightString(largura - 2 * cm, 0.8 * cm, f"Página {self._pageNumber} de {total_paginas}")
        self.restoreState()


def criar_canvas_numerado(texto_rodape: str):
    """Devolve uma subclasse de NumberedCanvas com o texto de rodapé informado."""
    class _CanvasComRodape(NumberedCanvas):
        _texto_rodape = texto_rodape
    return _CanvasComRodape


# ----------------------------------------------------------------------
# Estilos (criados uma única vez)
# ----------------------------------------------------------------------
_base = getSampleStyleSheet()

ESTILO_SUBTITULO = ParagraphStyle(
    'PdfSubtitulo', parent=_base['Normal'], fontSize=11,
    textColor=COR_MUTED, spaceAfter=20, alignment=1
)
ESTILO_TEXTO = ParagraphStyle(
    'PdfTexto', parent=_base['Normal'], fontName='Helvetica',
    fontSize=9, leading=11, textColor=colors.black
)
ESTILO_TEXTO_DIREITA = ParagraphStyle('PdfTextoDireita', parent=ESTILO_TEXTO, alignment=2)
ESTILO_TOTAL = ParagraphStyle(
    'PdfTotal', parent=ESTILO_TEXTO, fontName='Helvetica-Bold', alignment=2
)
ESTILO_CABECALHO = ParagraphStyle(
    'PdfCabecalho', parent=ESTILO_TEXTO, fontName='Helvetica-Bold',
    fontSize=10, leading=12, textColor=colors.white
)
ESTILO_CABECALHO_DIREITA = ParagraphStyle('PdfCabecalhoDireita', parent=ESTILO_CABECALHO, alignment=2)
ESTILO_VAZIO = ParagraphStyle(
    'PdfVazio', parent=ESTILO_TEXTO, fontName='Helvetica-Oblique',
    fontSize=10, textColor=COR_MUTED, spaceAfter=10
)


def _estilo_titulo(nome: str, cor) -> ParagraphStyle:
    return ParagraphStyle(nome, parent=_base['Heading1'], fontSize=18,
                          textColor=cor, spaceAfter=12, alignment=1)


def _estilo_secao(nome: str, cor) -> ParagraphStyle:
    return ParagraphStyle(nome, parent=_base['Heading2'], fontSize=13,
                          textColor=cor, spaceBefore=16, spaceAfter=8)


ESTILO_TITULO_GASTOS = _estilo_titulo('PdfTituloGastos', COR_AMBER)
ESTILO_TITULO_RECEITAS = _estilo_titulo('PdfTituloReceitas', COR_GREEN)
ESTILO_TITULO_CONSOLIDADO = _estilo_titulo('PdfTituloConsolidado', COR_AMBER)
ESTILO_SECAO_RECEITAS = _estilo_secao('PdfSecaoReceitas', COR_GREEN)
ESTILO_SECAO_DESPESAS = _estilo_secao('PdfSecaoDespesas', COR_AMBER)


# ----------------------------------------------------------------------
# Construção de tabelas e documento
# ----------------------------------------------------------------------
def _tabela_lancamentos(cabecalho, linhas, larguras, cor_header, cor_linha, total=None) -> Table:
    """
    Monta uma tabela de lançamentos com cabeçalho repetido a cada página.
    - cabecalho: lista de títulos (a última coluna é tratada como valor, alinhada à direita)
    - linhas: lista de listas de strings (texto cru; o escape é feito aqui)
    - total: string já formatada (ex.: 'R$ 1.250,00') para adicionar a linha de TOTAL
    """
    ultima = len(cabecalho) - 1
    dados = [[
        Paragraph(esc(t), ESTILO_CABECALHO_DIREITA if i == ultima else ESTILO_CABECALHO)
        for i, t in enumerate(cabecalho)
    ]]
    for linha in linhas:
        dados.append([
            Paragraph(esc(c), ESTILO_TEXTO_DIREITA if i == ultima else ESTILO_TEXTO)
            for i, c in enumerate(linha)
        ])

    estilos = [
        ('BACKGROUND', (0, 0), (-1, 0), cor_header),
        ('LINEBELOW', (0, 0), (-1, 0), 1, cor_linha),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]

    ultimo_dado = len(dados) - 1
    if linhas:
        estilos += [
            ('ROWBACKGROUNDS', (0, 1), (-1, ultimo_dado), [colors.white, COR_ZEBRA]),
            ('GRID', (0, 0), (-1, ultimo_dado), 0.5, COR_BORDER),
        ]

    if total is not None:
        linha_total = [Paragraph('TOTAL', ESTILO_TOTAL)] + [''] * (ultima - 1) + [Paragraph(esc(total), ESTILO_TOTAL)]
        dados.append(linha_total)
        estilos += [
            ('SPAN', (0, -1), (ultima - 1, -1)),
            ('BACKGROUND', (0, -1), (-1, -1), COR_TOTAL_BG),
            ('LINEABOVE', (0, -1), (-1, -1), 1, COR_DARK),
            ('TOPPADDING', (0, -1), (-1, -1), 8),
            ('BOTTOMPADDING', (0, -1), (-1, -1), 8),
        ]

    tabela = Table(dados, colWidths=larguras, repeatRows=1)
    tabela.setStyle(TableStyle(estilos))
    return tabela


def _bloco_lancamentos(cabecalho, linhas, larguras, cor_header, cor_linha, total=None):
    """Tabela de lançamentos ou, se não houver nenhum, uma mensagem."""
    if not linhas:
        return Paragraph("Nenhum lançamento no período.", ESTILO_VAZIO)
    return _tabela_lancamentos(cabecalho, linhas, larguras, cor_header, cor_linha, total)


def _cabecalho_relatorio(titulo, estilo_titulo, nome_usuario, periodo_str):
    return [
        Paragraph(esc(titulo), estilo_titulo),
        Paragraph(f"Usuário: {esc(nome_usuario)}  |  Período: {esc(periodo_str)}", ESTILO_SUBTITULO),
        Spacer(1, 0.5 * cm),
    ]


def _construir_pdf(elements, titulo_pdf: str, estabelecimento: str) -> io.BytesIO:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=2 * cm, leftMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
        title=_sanear(titulo_pdf), author=_sanear(estabelecimento),
    )
    rodape = f"{estabelecimento} — Gerado em {datetime.now().strftime('%d/%m/%Y às %H:%M')}"
    doc.build(elements, canvasmaker=criar_canvas_numerado(rodape))
    buffer.seek(0)
    return buffer


# ----------------------------------------------------------------------
# Linhas formatadas
# ----------------------------------------------------------------------
def _ordenados(itens, campo_data: str):
    return sorted(itens or [], key=lambda i: getattr(i, campo_data))


def _nome_categoria(gasto) -> str:
    categoria = getattr(gasto, 'categoria', None)
    return (getattr(categoria, 'nome_categoria', None) or '—') if categoria else '—'


def _linha_gasto(g, com_pagamento: bool) -> list:
    linha = [_fmt_data(g.data_gasto), g.descricao or '—', _nome_categoria(g)]
    if com_pagamento:
        linha.append(g.forma_pagamento or '—')
    linha.append(brl(g.valor))
    return linha


def _linha_receita(r, com_recebimento: bool) -> list:
    linha = [_fmt_data(r.data_receita), r.descricao or '—', r.tipo_receita or '—']
    if com_recebimento:
        linha.append(r.forma_recebimento or '—')
    linha.append(brl(r.valor))
    return linha


class PdfService:

    @staticmethod
    def gerar_relatorio_gastos(gastos, periodo_str: str, nome_usuario: str,
                               estabelecimento: str = NOME_ESTABELECIMENTO) -> io.BytesIO:
        """Gera PDF de relatório de despesas."""
        gastos = _ordenados(gastos, 'data_gasto')
        titulo = f"Relatório de Despesas — {estabelecimento}"

        elements = _cabecalho_relatorio(titulo, ESTILO_TITULO_GASTOS, nome_usuario, periodo_str)
        elements.append(_bloco_lancamentos(
            ['Data', 'Descrição', 'Categoria', 'Pagamento', 'Valor (R$)'],
            [_linha_gasto(g, com_pagamento=True) for g in gastos],
            [2.3 * cm, 4.7 * cm, 3.5 * cm, 3.5 * cm, 3.0 * cm],
            COR_DARK, COR_AMBER, total=brl(_somar(gastos)),
        ))
        return _construir_pdf(elements, titulo, estabelecimento)

    @staticmethod
    def gerar_relatorio_receitas(receitas, periodo_str: str, nome_usuario: str,
                                 estabelecimento: str = NOME_ESTABELECIMENTO) -> io.BytesIO:
        """Gera PDF de relatório de receitas."""
        receitas = _ordenados(receitas, 'data_receita')
        titulo = f"Relatório de Receitas — {estabelecimento}"

        elements = _cabecalho_relatorio(titulo, ESTILO_TITULO_RECEITAS, nome_usuario, periodo_str)
        elements.append(_bloco_lancamentos(
            ['Data', 'Descrição', 'Tipo', 'Recebimento', 'Valor (R$)'],
            [_linha_receita(r, com_recebimento=True) for r in receitas],
            [2.3 * cm, 4.7 * cm, 3.5 * cm, 3.5 * cm, 3.0 * cm],
            COR_GREEN_ESCURO, COR_GREEN, total=brl(_somar(receitas)),
        ))
        return _construir_pdf(elements, titulo, estabelecimento)

    @staticmethod
    def gerar_relatorio_consolidado(gastos, receitas, periodo_str: str, nome_usuario: str,
                                    estabelecimento: str = NOME_ESTABELECIMENTO) -> io.BytesIO:
        """Gera PDF consolidado com receitas + despesas + saldo do período."""
        gastos = _ordenados(gastos, 'data_gasto')
        receitas = _ordenados(receitas, 'data_receita')
        titulo = f"Relatório Consolidado — {estabelecimento}"

        total_gastos = _somar(gastos)
        total_receitas = _somar(receitas)
        saldo = total_receitas - total_gastos

        elements = _cabecalho_relatorio(titulo, ESTILO_TITULO_CONSOLIDADO, nome_usuario, periodo_str)

        # Resumo
        estilo_saldo = ParagraphStyle(
            'PdfSaldo', parent=ESTILO_TOTAL,
            textColor=COR_GREEN if saldo >= 0 else COR_RED
        )
        resumo = [
            [Paragraph('Indicador', ESTILO_CABECALHO), Paragraph('Valor', ESTILO_CABECALHO_DIREITA)],
            [Paragraph('Total de Receitas', ESTILO_TEXTO), Paragraph(esc(brl(total_receitas)), ESTILO_TEXTO_DIREITA)],
            [Paragraph('Total de Despesas', ESTILO_TEXTO), Paragraph(esc(brl(total_gastos)), ESTILO_TEXTO_DIREITA)],
            [Paragraph('<b>Saldo do período</b>', ESTILO_TEXTO), Paragraph(esc(brl(saldo)), estilo_saldo)],
        ]
        t_resumo = Table(resumo, colWidths=[10 * cm, 7 * cm])
        t_resumo.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), COR_DARK),
            ('GRID', (0, 0), (-1, -1), 0.5, COR_BORDER),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]))
        elements.append(t_resumo)

        # Receitas
        elements.append(Spacer(1, 0.5 * cm))
        elements.append(Paragraph("Receitas", ESTILO_SECAO_RECEITAS))
        elements.append(_bloco_lancamentos(
            ['Data', 'Descrição', 'Tipo', 'Valor (R$)'],
            [_linha_receita(r, com_recebimento=False) for r in receitas],
            [2.5 * cm, 7.0 * cm, 4.0 * cm, 3.5 * cm],
            COR_GREEN_ESCURO, COR_GREEN,
        ))

        # Despesas
        elements.append(Spacer(1, 0.5 * cm))
        elements.append(Paragraph("Despesas", ESTILO_SECAO_DESPESAS))
        elements.append(_bloco_lancamentos(
            ['Data', 'Descrição', 'Categoria', 'Valor (R$)'],
            [_linha_gasto(g, com_pagamento=False) for g in gastos],
            [2.5 * cm, 7.0 * cm, 4.0 * cm, 3.5 * cm],
            COR_DARK, COR_AMBER,
        ))
        return _construir_pdf(elements, titulo, estabelecimento)