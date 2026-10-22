"""
ExcelService — gera relatórios .xlsx com openpyxl (dependência já instalada).
Produz um arquivo de múltiplas abas: Despesas, Receitas, Resumo.
"""
import io
from datetime import date
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import (Font, PatternFill, Alignment, Border, Side,
                              GradientFill)
from openpyxl.utils import get_column_letter
from openpyxl.chart import BarChart, Reference


# ── Paleta de cores ──────────────────────────────────────────────────────────
COR_HEADER_DESPESA = "C0392B"   # vermelho
COR_HEADER_RECEITA = "1E8449"   # verde
COR_HEADER_RESUMO  = "B7950B"   # dourado
COR_LINHA_PAR      = "F5F5F5"
COR_LINHA_IMPAR    = "FFFFFF"
COR_TOTAL          = "FCF3CF"

THIN = Side(style='thin', color='CCCCCC')
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _header_style(cor_hex: str) -> dict:
    return {
        'font': Font(bold=True, color='FFFFFF', size=11),
        'fill': PatternFill('solid', fgColor=cor_hex),
        'alignment': Alignment(horizontal='center', vertical='center', wrap_text=True),
        'border': BORDER,
    }


def _apply(cell, **kwargs):
    for attr, val in kwargs.items():
        setattr(cell, attr, val)


def _auto_width(ws, extra: int = 4):
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            try:
                if cell.value:
                    max_len = max(max_len, len(str(cell.value)))
            except Exception:
                pass
        ws.column_dimensions[col_letter].width = min(max_len + extra, 50)


class ExcelService:

    @staticmethod
    def gerar_relatorio(gastos, receitas, periodo_str: str, nome_usuario: str) -> io.BytesIO:
        wb = Workbook()

        # Remove aba default
        wb.remove(wb.active)

        ExcelService._aba_despesas(wb, gastos, periodo_str)
        ExcelService._aba_receitas(wb, receitas, periodo_str)
        ExcelService._aba_resumo(wb, gastos, receitas, periodo_str, nome_usuario)

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf

    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _aba_despesas(wb: Workbook, gastos, periodo_str: str):
        ws = wb.create_sheet("💸 Despesas")
        ws.freeze_panes = "A2"

        # Cabeçalho
        headers = ['Data', 'Descrição', 'Categoria', 'Forma Pgto', 'Parcela', 'Valor (R$)', 'Pago?']
        hs = _header_style(COR_HEADER_DESPESA)
        for col_idx, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_idx, value=h)
            _apply(cell, **hs)

        # Dados
        total = Decimal('0')
        for row_idx, g in enumerate(gastos, 2):
            fill_color = COR_LINHA_PAR if row_idx % 2 == 0 else COR_LINHA_IMPAR
            row_fill = PatternFill('solid', fgColor=fill_color)

            parcela_str = f"{g.parcela_atual}/{g.total_parcelas}" if g.is_parcelado else "—"
            pago_str = "✓" if getattr(g, 'is_pago', True) else "Pendente"
            row_data = [
                g.data_gasto.strftime('%d/%m/%Y'),
                g.descricao or '—',
                g.categoria.nome_categoria,
                g.forma_pagamento,
                parcela_str,
                float(g.valor),
                pago_str,
            ]
            for col_idx, val in enumerate(row_data, 1):
                cell = ws.cell(row=row_idx, column=col_idx, value=val)
                cell.fill = row_fill
                cell.border = BORDER
                cell.alignment = Alignment(vertical='center')
                if col_idx == 6:  # valor
                    cell.number_format = 'R$ #,##0.00'
            total += g.valor

        # Linha de total
        total_row = len(gastos) + 2
        ws.cell(row=total_row, column=5, value="TOTAL").font = Font(bold=True)
        total_cell = ws.cell(row=total_row, column=6, value=float(total))
        total_cell.font = Font(bold=True, color=COR_HEADER_DESPESA)
        total_cell.fill = PatternFill('solid', fgColor=COR_TOTAL)
        total_cell.number_format = 'R$ #,##0.00'
        total_cell.border = BORDER

        _auto_width(ws)

    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _aba_receitas(wb: Workbook, receitas, periodo_str: str):
        ws = wb.create_sheet("💰 Receitas")
        ws.freeze_panes = "A2"

        headers = ['Data', 'Descrição', 'Tipo', 'Forma Recebimento', 'Valor (R$)']
        hs = _header_style(COR_HEADER_RECEITA)
        for col_idx, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_idx, value=h)
            _apply(cell, **hs)

        total = Decimal('0')
        for row_idx, r in enumerate(receitas, 2):
            fill_color = COR_LINHA_PAR if row_idx % 2 == 0 else COR_LINHA_IMPAR
            row_fill = PatternFill('solid', fgColor=fill_color)
            row_data = [
                r.data_receita.strftime('%d/%m/%Y'),
                r.descricao or '—',
                r.tipo_receita or 'Outros',
                r.forma_recebimento,
                float(r.valor),
            ]
            for col_idx, val in enumerate(row_data, 1):
                cell = ws.cell(row=row_idx, column=col_idx, value=val)
                cell.fill = row_fill
                cell.border = BORDER
                cell.alignment = Alignment(vertical='center')
                if col_idx == 5:
                    cell.number_format = 'R$ #,##0.00'
            total += r.valor

        total_row = len(receitas) + 2
        ws.cell(row=total_row, column=4, value="TOTAL").font = Font(bold=True)
        total_cell = ws.cell(row=total_row, column=5, value=float(total))
        total_cell.font = Font(bold=True, color=COR_HEADER_RECEITA)
        total_cell.fill = PatternFill('solid', fgColor=COR_TOTAL)
        total_cell.number_format = 'R$ #,##0.00'
        total_cell.border = BORDER

        _auto_width(ws)

    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _aba_resumo(wb: Workbook, gastos, receitas, periodo_str: str, nome_usuario: str):
        ws = wb.create_sheet("📊 Resumo")

        total_desp = sum(g.valor for g in gastos) if gastos else Decimal('0')
        total_rec  = sum(r.valor for r in receitas) if receitas else Decimal('0')
        saldo      = total_rec - total_desp

        # Título
        ws.merge_cells('A1:C1')
        title_cell = ws.cell(row=1, column=1, value=f"Resumo Financeiro — {periodo_str}")
        title_cell.font = Font(bold=True, size=14, color=COR_HEADER_RESUMO)
        title_cell.alignment = Alignment(horizontal='center')

        ws.cell(row=2, column=1, value=f"Gerado por: {nome_usuario}")
        ws.cell(row=2, column=3, value=f"Data: {date.today().strftime('%d/%m/%Y')}")

        # KPIs
        kpis = [
            ("Total de Receitas", float(total_rec), COR_HEADER_RECEITA),
            ("Total de Despesas", float(total_desp), COR_HEADER_DESPESA),
            ("Saldo do Período", float(saldo), "1A5276" if saldo >= 0 else COR_HEADER_DESPESA),
            ("Nº de Lançamentos", len(gastos) + len(receitas), "34495E"),
        ]
        hs = _header_style(COR_HEADER_RESUMO)
        for col_idx, (label, val, cor) in enumerate(kpis, 1):
            label_cell = ws.cell(row=4, column=col_idx, value=label)
            _apply(label_cell, **hs)
            val_cell = ws.cell(row=5, column=col_idx, value=val)
            val_cell.font = Font(bold=True, size=13, color=cor)
            val_cell.fill = PatternFill('solid', fgColor='FDFEFE')
            val_cell.border = BORDER
            val_cell.alignment = Alignment(horizontal='center')
            if col_idx in [1, 2, 3]:
                val_cell.number_format = 'R$ #,##0.00'

        # Despesas por categoria
        ws.cell(row=7, column=1, value="Despesas por Categoria").font = Font(bold=True, size=11)
        cat_map = {}
        for g in gastos:
            nome = g.categoria.nome_categoria
            cat_map[nome] = cat_map.get(nome, Decimal('0')) + g.valor

        for row_idx, (cat, val) in enumerate(sorted(cat_map.items(), key=lambda x: -x[1]), 8):
            ws.cell(row=row_idx, column=1, value=cat)
            val_cell = ws.cell(row=row_idx, column=2, value=float(val))
            val_cell.number_format = 'R$ #,##0.00'

        _auto_width(ws)
        ws.column_dimensions['A'].width = 28
