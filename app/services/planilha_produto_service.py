import io
import math
import re
import logging
from datetime import datetime
import numpy as np
import pandas as pd
import pdfplumber
from PIL import Image, ImageOps
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph
)
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas

from app.services.bacen_service import BacenService

logger = logging.getLogger(__name__)

# Instância preguiçosa do RapidOCR para processar imagens/prints locais
import threading as _threading
_OCR_LOCK = _threading.Lock()
_OCR_INFERENCE_LOCK = _threading.Lock()
_OCR_ENGINE = None

# Linha de lista de preços lida por OCR. Aceita variações reais do OCR:
#   "• Banoffe - 10,95" | "Banoffe-10.95" | "Gelatina diet de amora -5,78" | "Banoffe R$ 10,42"
# O preço é capturado SEM sinal: um "-" colado no preço é separador, não número negativo.
_LINHA_PRODUTO_PRECO = re.compile(
    r'^[\s•·.:+*\-–—]*(?P<nome>[^\d\s].*?)\s*[-–—:]?\s*(?:R\$\s*)?'
    r'(?P<preco>\d{1,3}(?:\.\d{3})*,\d{2}|\d+[.,]\d{2})\s*$'
)
# Fallback: último valor monetário da linha (tabelas com colunas extras depois do preço)
_PRECO_QUALQUER = re.compile(r'(?:R\$\s*)?(\d{1,3}(?:\.\d{3})*,\d{2}|\d+[.,]\d{2})(?!\d)')


def _obter_ocr_engine():
    with _OCR_LOCK:
        return _criar_ocr_engine()


def _executar_ocr(imagem):
    engine = _obter_ocr_engine()

    with _OCR_INFERENCE_LOCK:
        try:
            return engine(imagem, use_cls=False)
        except TypeError:
            return engine(imagem)


def _criar_ocr_engine():
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _OCR_ENGINE = RapidOCR()
        except Exception as e:
            logger.error(f"Não foi possível inicializar RapidOCR: {e}")
            raise RuntimeError(f"Motor de OCR não disponível: {e}")
    return _OCR_ENGINE


class NumberedCanvas(canvas.Canvas):
    """Canvas com numeração de páginas 'Página X de Y' no rodapé."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_number(num_pages)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

    def draw_page_number(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748b"))

        self.setStrokeColor(colors.HexColor("#e2e8f0"))
        self.setLineWidth(0.5)
        self.line(1.5 * cm, 1.0 * cm, self._pagesize[0] - 1.5 * cm, 1.0 * cm)

        texto_esq = f"Doce como Mel — Tabela de Preços com Correção IPCA Oficial (BACEN SGS 433) | Gerado em {datetime.now().strftime('%d/%m/%Y às %H:%M')}"
        texto_dir = f"Página {self._pageNumber} de {page_count}"

        self.drawString(1.5 * cm, 0.65 * cm, texto_esq)
        self.drawRightString(self._pagesize[0] - 1.5 * cm, 0.65 * cm, texto_dir)
        self.restoreState()


class PlanilhaProdutoService:
    # Variações comuns de nomes de colunas
    COLUNAS_PRODUTO_ALIASES = [
        'produto', 'nome', 'descricao', 'descrição', 'item', 'titulo',
        'mercadoria', 'artigo', 'servico', 'serviço', 'especificacao',
        'especificação', 'nome_produto', 'nome do produto', 'produtos'
    ]

    COLUNAS_PRECO_ALIASES = [
        'preco', 'preço', 'valor', 'preco_venda', 'preço_venda',
        'preco_unitario', 'preço_unitário', 'preco_atual', 'preço_atual',
        'valor_unitario', 'valor_unitário', 'valor_venda', 'preco_tabela',
        'custo', 'preco_custo', 'preco_original', 'preço_original',
        'precos', 'preços', 'valores'
    ]

    COLUNAS_CODIGO_ALIASES = [
        'codigo', 'código', 'id', 'cod', 'sku', 'ref', 'referencia', 'referência'
    ]

    COLUNAS_CATEGORIA_ALIASES = [
        'categoria', 'tipo', 'grupo', 'departamento', 'secao', 'seção', 'linha'
    ]

    @classmethod
    def normalizar_texto(cls, texto) -> str:
        """Remove acentos e caracteres especiais para comparação flexível."""
        if isinstance(texto, (pd.Series, list, tuple)):
            texto = texto[0] if len(texto) > 0 else ''
        if not isinstance(texto, str):
            texto = str(texto) if (texto is not None and pd.notna(texto)) else ''
        t = texto.strip().lower()
        t = re.sub(r'[áàâãä]', 'a', t)
        t = re.sub(r'[éèêë]', 'e', t)
        t = re.sub(r'[íìîï]', 'i', t)
        t = re.sub(r'[óòôõö]', 'o', t)
        t = re.sub(r'[úùûü]', 'u', t)
        t = re.sub(r'[ç]', 'c', t)
        t = re.sub(r'[^a-z0-9]', '', t)
        return t

    @classmethod
    def deduplicar_colunas(cls, colunas) -> list[str]:
        """Garante que todos os nomes de colunas sejam strings únicas e limpas."""
        contagem = {}
        novas = []
        for i, c in enumerate(colunas):
            if c is not None and not (isinstance(c, float) and math.isnan(c)) and str(c).strip() != '':
                nome = str(c).strip()
            else:
                nome = f"Coluna_{i+1}"

            if nome in contagem:
                contagem[nome] += 1
                novas.append(f"{nome}_{contagem[nome]}")
            else:
                contagem[nome] = 0
                novas.append(nome)
        return novas

    @classmethod
    def _obter_escalar(cls, val, default: str = "") -> str:
        """Garante retorno de string escalar segura, desempacotando Series caso ocorra."""
        if isinstance(val, (pd.Series, list, tuple)):
            for v in val:
                if v is not None and not pd.isna(v) and str(v).strip() != '':
                    return str(v).strip()
            return default
        if val is None or pd.isna(val):
            return default
        s = str(val).strip()
        return s if s != '' else default

    @classmethod
    def identificar_colunas(cls, colunas: list[str]) -> dict:
        """
        Identifica automaticamente quais colunas correspondem a Produto, Preço, Código e Categoria.
        """
        mapeamento = {
            'produto': None,
            'preco': None,
            'codigo': None,
            'categoria': None
        }

        colunas_norm = {col: cls.normalizar_texto(col) for col in colunas}

        # 1. Procura Coluna de Preço (prioritário)
        for col_orig, col_n in colunas_norm.items():
            for alias in cls.COLUNAS_PRECO_ALIASES:
                if cls.normalizar_texto(alias) == col_n or cls.normalizar_texto(alias) in col_n:
                    mapeamento['preco'] = col_orig
                    break
            if mapeamento['preco']:
                break

        # 2. Procura Coluna de Produto
        for col_orig, col_n in colunas_norm.items():
            if col_orig == mapeamento['preco']:
                continue
            for alias in cls.COLUNAS_PRODUTO_ALIASES:
                if cls.normalizar_texto(alias) == col_n or cls.normalizar_texto(alias) in col_n:
                    mapeamento['produto'] = col_orig
                    break
            if mapeamento['produto']:
                break

        # 3. Procura Código (opcional)
        for col_orig, col_n in colunas_norm.items():
            if col_orig in (mapeamento['preco'], mapeamento['produto']):
                continue
            for alias in cls.COLUNAS_CODIGO_ALIASES:
                if cls.normalizar_texto(alias) == col_n:
                    mapeamento['codigo'] = col_orig
                    break
            if mapeamento['codigo']:
                break

        # 4. Procura Categoria (opcional)
        for col_orig, col_n in colunas_norm.items():
            if col_orig in (mapeamento['preco'], mapeamento['produto'], mapeamento['codigo']):
                continue
            for alias in cls.COLUNAS_CATEGORIA_ALIASES:
                if cls.normalizar_texto(alias) == col_n:
                    mapeamento['categoria'] = col_orig
                    break
            if mapeamento['categoria']:
                break

        # Fallback se não encontrou coluna de produto, usa a primeira coluna não de preço
        if not mapeamento['produto'] and colunas:
            for c in colunas:
                if c != mapeamento['preco']:
                    mapeamento['produto'] = c
                    break

        return mapeamento

    @classmethod
    def converter_valor_monetario(cls, valor) -> float | None:
        """
        Converte string, número ou item de Series para float.
        Ex: 'R$ 1.250,50' -> 1250.50, '15,90' -> 15.90, 15.9 -> 15.9
        """
        if isinstance(valor, (pd.Series, list, tuple)):
            for v in valor:
                res = cls.converter_valor_monetario(v)
                if res is not None:
                    return res
            return None

        if valor is None or pd.isna(valor):
            return None

        if isinstance(valor, (int, float)):
            return float(valor)

        texto = str(valor).strip()
        if not texto:
            return None

        texto = texto.replace('R$', '').replace('r$', '').replace('\xa0', '').strip()

        if ',' in texto:
            texto = texto.replace('.', '')
            texto = texto.replace(',', '.')
        else:
            if re.search(r'\d+,\d{3}', texto):
                texto = texto.replace(',', '')

        try:
            val = float(re.sub(r'[^\d.-]', '', texto))
            return val
        except (ValueError, TypeError):
            return None

    @classmethod
    def aplicar_regra_arredondamento(cls, valor: float, regra: str = 'padrao') -> float:
        """
        Aplica regras de precificação mercadológica:
        - 'padrao': 2 casas decimais normais (ex: R$ 15,48)
        - 'termina_99': arredonda para centavos .99 (ex: R$ 15,99)
        - 'termina_90': arredonda para centavos .90 (ex: R$ 15,90)
        - 'inteiro': número inteiro mais próximo (ex: R$ 15,00)
        """
        if regra == 'termina_99':
            if valor <= 1.0:
                return round(valor, 2)
            base = math.floor(valor)
            if (valor - base) > 0.99:
                return float(base + 1.99)
            return float(base + 0.99)

        elif regra == 'termina_90':
            if valor <= 1.0:
                return round(valor, 2)
            base = math.floor(valor)
            if (valor - base) > 0.90:
                return float(base + 1.90)
            return float(base + 0.90)

        elif regra == 'inteiro':
            return float(round(valor))

        return round(valor, 2)

    @classmethod
    def _validar_mes_vigencia(cls, mes_vigencia: str | None) -> tuple[int, int]:
        """
        Converte 'AAAA-MM' (formato do <input type="month">) em (ano, mes).
        Sem valor, usa o mês atual.
        """
        if not mes_vigencia:
            agora = datetime.now()
            return agora.year, agora.month
        try:
            ano_str, mes_str = str(mes_vigencia).strip().split('-')
            ano, mes = int(ano_str), int(mes_str)
            if 1 <= mes <= 12 and 2000 <= ano <= 2100:
                return ano, mes
        except ValueError:
            pass
        raise ValueError("Mês de vigência inválido. Use o formato AAAA-MM (ex.: 2026-11).")

    @classmethod
    def _detectar_e_ajustar_cabecalho(cls, df_raw: pd.DataFrame) -> pd.DataFrame:
        """
        Identifica em qual linha está o cabeçalho real caso haja banners, títulos ou linhas vazias antes da tabela.
        Garante que todos os nomes de colunas sejam strings deduplicadas.
        """
        if df_raw.empty:
            return df_raw

        max_linhas = min(12, len(df_raw))
        melhor_linha = -1
        max_matches = 0

        aliases_todos = cls.COLUNAS_PRECO_ALIASES + cls.COLUNAS_PRODUTO_ALIASES + cls.COLUNAS_CODIGO_ALIASES

        for idx in range(max_linhas):
            row_vals = [cls.normalizar_texto(v) for v in df_raw.iloc[idx]]
            matches = sum(1 for val in row_vals if any(cls.normalizar_texto(a) == val or cls.normalizar_texto(a) in val for a in aliases_todos))
            if matches > max_matches:
                max_matches = matches
                melhor_linha = idx

        if melhor_linha >= 0 and max_matches > 0:
            raw_cols = df_raw.iloc[melhor_linha].tolist()
            novas_colunas = cls.deduplicar_colunas(raw_cols)
            df_ajustado = df_raw.iloc[melhor_linha + 1:].reset_index(drop=True)
            df_ajustado.columns = novas_colunas

            # Se alguma linha de dados for repetição do cabeçalho, descarta
            filtro = []
            for _, r in df_ajustado.iterrows():
                r_vals = [str(v).strip() for v in r.tolist()]
                if r_vals == [str(c).strip() for c in raw_cols]:
                    filtro.append(False)
                else:
                    filtro.append(True)
            df_ajustado = df_ajustado[filtro].reset_index(drop=True)
            return df_ajustado

        primeira_linha = df_raw.iloc[0].tolist()
        tem_preco_linha_0 = any(cls.converter_valor_monetario(v) is not None for v in primeira_linha)
        if tem_preco_linha_0:
            if len(df_raw.columns) == 2:
                novas_colunas = ['Produto', 'Preço']
            elif len(df_raw.columns) == 3:
                novas_colunas = ['Código', 'Produto', 'Preço']
            else:
                novas_colunas = [f'Coluna_{i+1}' for i in range(len(df_raw.columns))]
            df_raw.columns = cls.deduplicar_colunas(novas_colunas)
            return df_raw
        else:
            df_raw.columns = cls.deduplicar_colunas(primeira_linha)
            return df_raw.iloc[1:].reset_index(drop=True)

    @classmethod
    def _preparar_imagem_para_ocr(cls, conteudo: bytes):
        """Abre a imagem, corrige a rotação (fotos de celular) e amplia imagens pequenas.
        Prints pequenos (ex.: 215 px de largura) perdem espaços e vírgulas no OCR sem isso."""
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(conteudo))).convert('RGB')
        if max(img.size) > 1600:
            img.thumbnail((1600, 1600), Image.LANCZOS)
        fator = min(3, max(1, round(1600 / max(img.size))))
        if fator > 1:
            img = img.resize((img.width * fator, img.height * fator), Image.LANCZOS)
        return np.array(img)

    @classmethod
    def ler_arquivo_imagem(cls, stream) -> pd.DataFrame:
        """
        Extrai produtos e preços de fotos e prints via OCR (RapidOCR).
        Funciona para tabelas (produto e preço em caixas separadas) e para listas
        em texto corrido ("Produto - 10,95"), onde o OCR devolve a linha inteira numa caixa só.
        """
        engine = _obter_ocr_engine()
        conteudo = stream.read()
        stream.seek(0)

        try:
            imagem = cls._preparar_imagem_para_ocr(conteudo)
        except Exception:
            raise ValueError("Não foi possível abrir a imagem enviada. Envie um arquivo PNG, JPG ou WEBP válido.")

        res, _ = _executar_ocr(imagem)
        if not res:
            raise ValueError("Não foi possível detectar texto ou números na imagem enviada. Certifique-se de que a imagem esteja nítida.")

        caixas = []
        for box, texto, *_ in res:
            texto = str(texto).strip()
            if not texto:
                continue
            ys = [p[1] for p in box]
            xs = [p[0] for p in box]
            caixas.append({'texto': texto, 'y': (min(ys) + max(ys)) / 2.0,
                           'x': min(xs), 'h': max(ys) - min(ys)})
        if not caixas:
            raise ValueError("Não foi possível detectar texto ou números na imagem enviada. Certifique-se de que a imagem esteja nítida.")

        # Tolerância de "mesma linha" proporcional à altura do texto (não mais 18 px fixos)
        altura_mediana = sorted(c['h'] for c in caixas)[len(caixas) // 2]
        tolerancia = max(4.0, 0.6 * altura_mediana)

        caixas.sort(key=lambda c: c['y'])
        linhas = []
        for c in caixas:
            if linhas and abs(linhas[-1]['y'] - c['y']) <= tolerancia:
                linhas[-1]['itens'].append(c)
                linhas[-1]['y'] += (c['y'] - linhas[-1]['y']) / len(linhas[-1]['itens'])
            else:
                linhas.append({'y': c['y'], 'itens': [c]})

        cabecalhos = ('produto', 'produtos', 'item', 'descricao', 'nome')
        produtos_extraidos = []
        sem_preco = []
        for linha in linhas:
            texto_linha = ' '.join(i['texto'] for i in sorted(linha['itens'], key=lambda i: i['x'])).strip()

            m = _LINHA_PRODUTO_PRECO.match(texto_linha)
            if m:
                nome, preco = m['nome'], m['preco']
            else:
                achados = list(_PRECO_QUALQUER.finditer(texto_linha))
                if not achados:
                    sem_preco.append(texto_linha)
                    continue
                ultimo = achados[-1]
                nome, preco = texto_linha[:ultimo.start()], ultimo.group(1)

            nome = nome.strip(' •·.:+*-–—|;\t')
            if not re.search(r'[A-Za-zÀ-ÿ]', nome) or cls.normalizar_texto(nome) in cabecalhos:
                continue
            produtos_extraidos.append([nome, preco])

        logger.info("OCR de imagem: %d produtos; linhas sem preço ignoradas: %s",
                    len(produtos_extraidos), sem_preco)

        if not produtos_extraidos:
            raise ValueError("Não foi possível identificar pares de produto e preço na imagem/print. Verifique se a imagem contém preços visíveis com centavos.")

        return pd.DataFrame(produtos_extraidos, columns=['Produto', 'Preço'])

    @classmethod
    def ler_arquivo_pdf(cls, stream) -> pd.DataFrame:
        """
        Extrai tabelas de arquivos PDF utilizando pdfplumber.
        """
        linhas_brutas = []

        with pdfplumber.open(stream) as pdf:
            for page in pdf.pages:
                raw_tables = page.extract_tables() or []
                valid_tables = [t for t in raw_tables if any(len(r) >= 2 for r in t)]

                if valid_tables:
                    for tabela in valid_tables:
                        for row in tabela:
                            if not row:
                                continue
                            row_clean = [str(cell).strip() if cell is not None else '' for cell in row]
                            if any(row_clean):
                                linhas_brutas.append(row_clean)
                else:
                    texto = page.extract_text() or ''
                    for line in texto.split('\n'):
                        line = line.strip()
                        if not line:
                            continue
                        m_preco = re.search(r'(\d{1,3}(?:\.\d{3})*,\d{2}|\d+\.\d{2})\s*$', line)
                        if m_preco:
                            preco_str = m_preco.group(1)
                            resto = line[:m_preco.start()].strip(' -:;\t|')
                            resto = re.sub(r'R\$\s*$', '', resto, flags=re.IGNORECASE).strip(' -:;\t|')
                            m_cod = re.match(r'^([A-Z0-9_-]{3,10})\s+[-–]?\s*(.+)$', resto, re.IGNORECASE)
                            if m_cod:
                                cod = m_cod.group(1)
                                prod = m_cod.group(2)
                                linhas_brutas.append([cod, prod, preco_str])
                            else:
                                linhas_brutas.append([resto, preco_str])

        if not linhas_brutas:
            raise ValueError("Não foi possível identificar nenhuma tabela ou lista de produtos legível no arquivo PDF enviado.")

        max_cols = max(len(r) for r in linhas_brutas)
        linhas_padronizadas = [r + [''] * (max_cols - len(r)) for r in linhas_brutas]

        df_raw = pd.DataFrame(linhas_padronizadas)
        return cls._detectar_e_ajustar_cabecalho(df_raw)

    @classmethod
    def ler_arquivo(cls, stream, nome_arquivo: str) -> pd.DataFrame:
        """
        Lê tabela nos formatos .png, .jpg, .jpeg, .webp (imagem/print), .pdf, .xlsx, .xls ou .csv.
        """
        ext = nome_arquivo.rsplit('.', 1)[-1].lower() if '.' in nome_arquivo else ''

        if ext in ('png', 'jpg', 'jpeg', 'webp'):
            return cls.ler_arquivo_imagem(stream)
        elif ext == 'pdf':
            return cls.ler_arquivo_pdf(stream)
        elif ext in ('xlsx', 'xls'):
            df_raw = pd.read_excel(stream, header=None)
            return cls._detectar_e_ajustar_cabecalho(df_raw)
        elif ext == 'csv':
            conteudo = stream.read()
            stream.seek(0)
            for encoding in ['utf-8-sig', 'utf-8', 'latin-1', 'cp1252']:
                for sep in [';', ',', '\t']:
                    try:
                        df_raw = pd.read_csv(io.BytesIO(conteudo), sep=sep, encoding=encoding, header=None)
                        if len(df_raw.columns) > 1 or len(df_raw) > 0:
                            return cls._detectar_e_ajustar_cabecalho(df_raw)
                    except Exception:
                        continue
            df_raw = pd.read_csv(io.BytesIO(conteudo), header=None)
            return cls._detectar_e_ajustar_cabecalho(df_raw)
        else:
            raise ValueError(f"Formato de arquivo '{ext}' não suportado. Envie uma Imagem (.png, .jpg, .jpeg), PDF (.pdf), Excel (.xlsx, .xls) ou CSV (.csv).")

    @classmethod
    def processar_reajuste(
        cls,
        arquivo_stream,
        nome_arquivo: str,
        modalidade: str = '12_meses',
        margem_adicional: float = 0.0,
        regra_arredondamento: str = 'padrao',
        coluna_preco_selecionada: str = None,
        taxa_manual: float = None,
        mes_vigencia: str = None
    ) -> dict:
        """
        Processa o reajuste de preços de acordo com o IPCA e gera arquivos de saída em PDF e Excel.
        """
        cls._validar_mes_vigencia(mes_vigencia)

        df = cls.ler_arquivo(arquivo_stream, nome_arquivo)
        if df.empty:
            raise ValueError("O documento ou imagem enviada não contém dados legíveis.")

        colunas = cls.deduplicar_colunas(df.columns)
        df.columns = colunas

        # Identificação inteligente de colunas
        mapeamento = cls.identificar_colunas(colunas)

        coluna_preco = coluna_preco_selecionada if coluna_preco_selecionada and coluna_preco_selecionada in colunas else mapeamento['preco']
        coluna_produto = mapeamento['produto']
        coluna_codigo = mapeamento['codigo']
        coluna_categoria = mapeamento['categoria']

        if not coluna_preco:
            raise ValueError(
                f"Não foi possível identificar a coluna de preço no documento. "
                f"Colunas encontradas: {', '.join(colunas)}. "
                f"Certifique-se de que há uma coluna com preços ou valores dos itens."
            )

        dados_indice = BacenService.calcular_indice_para_reajuste(modalidade, custom_taxa=taxa_manual)
        taxa_ipca = dados_indice['percentual']

        fator_ipca = 1.0 + (taxa_ipca / 100.0)
        fator_margem = 1.0 + (margem_adicional / 100.0)
        fator_total = fator_ipca * fator_margem
        variacao_total_pct = round((fator_total - 1.0) * 100.0, 2)

        linhas_resultado = []
        precos_originais = []
        precos_reajustados = []
        qtd_ignoradas = 0

        df_copia = df.copy()
        novos_precos_col = []
        variacoes_rs_col = []
        variacoes_pct_col = []

        for idx, row in df.iterrows():
            raw_preco = row[coluna_preco]
            preco_orig = cls.converter_valor_monetario(raw_preco)

            raw_prod = row[coluna_produto] if coluna_produto else None
            produto_nome = cls._obter_escalar(raw_prod, default=f"Item {idx + 1}")

            raw_cod = row[coluna_codigo] if coluna_codigo else None
            codigo = cls._obter_escalar(raw_cod, default="")

            raw_cat = row[coluna_categoria] if coluna_categoria else None
            categoria = cls._obter_escalar(raw_cat, default="")

            if preco_orig is None or preco_orig <= 0:
                qtd_ignoradas += 1
                novos_precos_col.append(raw_preco if (raw_preco is not None and not isinstance(raw_preco, pd.Series)) else '')
                variacoes_rs_col.append(0.0)
                variacoes_pct_col.append(0.0)
                continue

            novo_preco_bruto = preco_orig * fator_total
            novo_preco = cls.aplicar_regra_arredondamento(novo_preco_bruto, regra_arredondamento)
            dif_rs = round(novo_preco - preco_orig, 2)
            dif_pct = round(((novo_preco / preco_orig) - 1.0) * 100.0, 2)

            precos_originais.append(preco_orig)
            precos_reajustados.append(novo_preco)

            novos_precos_col.append(novo_preco)
            variacoes_rs_col.append(dif_rs)
            variacoes_pct_col.append(dif_pct)

            linhas_resultado.append({
                'linha': idx + 1,
                'codigo': codigo,
                'produto': produto_nome,
                'categoria': categoria,
                'preco_original': preco_orig,
                'preco_original_fmt': f"R$ {preco_orig:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'),
                'novo_preco': novo_preco,
                'novo_preco_fmt': f"R$ {novo_preco:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'),
                'diferenca_rs': dif_rs,
                'diferenca_rs_fmt': f"+R$ {dif_rs:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.') if dif_rs >= 0 else f"-R$ {abs(dif_rs):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'),
                'diferenca_pct': dif_pct,
                'diferenca_pct_fmt': f"{dif_pct:+.2f}%"
            })

        df_copia['Preço Reajustado (IPCA)'] = novos_precos_col
        df_copia['Aumento (R$)'] = variacoes_rs_col
        df_copia['Variação (%)'] = variacoes_pct_col

        total_produtos = len(precos_originais)
        soma_original = sum(precos_originais)
        soma_reajustada = sum(precos_reajustados)
        aumento_total_rs = soma_reajustada - soma_original

        kpis = {
            'total_produtos': total_produtos,
            'total_linhas': len(df),
            'linhas_ignoradas': qtd_ignoradas,
            'soma_original': round(soma_original, 2),
            'soma_reajustada': round(soma_reajustada, 2),
            'aumento_total_rs': round(aumento_total_rs, 2),
            'variacao_media_pct': variacao_total_pct,
            'taxa_ipca': taxa_ipca,
            'margem_adicional': margem_adicional,
            'descricao_indice': dados_indice['descricao'],
            'regra_arredondamento': regra_arredondamento,
            'coluna_preco_utilizada': coluna_preco,
            'colunas_disponiveis': colunas
        }

        # Geração do arquivo PDF no formato espelho comparativo solicitado pelo usuário
        pdf_bytes = cls.gerar_pdf_reajustado(
            linhas_resultado=linhas_resultado,
            kpis=kpis,
            mes_vigencia=mes_vigencia
        )

        excel_bytes = cls._gerar_excel_formatado(
            df_copia,
            coluna_preco,
            taxa_ipca,
            margem_adicional,
            dados_indice['descricao']
        )

        timestamp_nome = datetime.now().strftime('%Y%m%d_%H%M')

        return {
            'sucesso': True,
            'kpis': kpis,
            'linhas_previa': linhas_resultado[:100],
            'pdf_bytes': pdf_bytes,
            'excel_bytes': excel_bytes,
            'nome_arquivo_pdf': f"produtos_reajustados_ipca_{timestamp_nome}.pdf",
            'nome_arquivo_excel': f"produtos_reajustados_ipca_{timestamp_nome}.xlsx"
        }

    @classmethod
    def gerar_pdf_reajustado(
        cls,
        linhas_resultado: list[dict],
        kpis: dict,
        nome_estabelecimento: str = "FORNECEDOR DOCE COMO MEL",
        mes_vigencia: str = None
    ) -> bytes:
        """
        Gera um PDF comparativo exatamente no formato solicitado pelo usuário:
        - Barra superior azul escura: Coluna 1 | Coluna 2 | FORNECEDOR DOCE COMO MEL | Coluna 3 | Coluna 4
        - Cabeçalho cinza: Produto | Preço | [separador] | Produto | Preço
        - Linhas comparativas lado a lado: Original (esq) vs Corrigido pelo IPCA (dir)
        - Rodapé amarelo suave: Período anterior (esq) vs Período com IPCA atualizado (dir)
        """
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            leftMargin=1.5 * cm,
            rightMargin=1.5 * cm,
            topMargin=1.5 * cm,
            bottomMargin=1.5 * cm
        )

        # Largura útil total no A4 Landscape = 29.7cm - 3.0cm = 26.7cm
        # Col 1 (Prod Orig): 6.8 cm, Col 2 (Preco Orig): 2.8 cm
        # Col 3 (Separador): 7.5 cm
        # Col 4 (Prod Reaj): 6.8 cm, Col 5 (Preco Reaj): 2.8 cm
        col_widths = [6.8 * cm, 2.8 * cm, 7.5 * cm, 6.8 * cm, 2.8 * cm]

        # Estilos tipográficos
        th_col = ParagraphStyle('ThCol', fontName='Helvetica', fontSize=8, textColor=colors.HexColor('#cbd5e1'), alignment=1)
        th_brand = ParagraphStyle('ThBrand', fontName='Helvetica-Bold', fontSize=10.5, textColor=colors.HexColor('#fbbf24'), alignment=1)

        th_sub = ParagraphStyle('ThSub', fontName='Helvetica-Bold', fontSize=9.5, textColor=colors.HexColor('#1f2937'), alignment=1)

        td_prod = ParagraphStyle('TdProd', fontName='Helvetica-Bold', fontSize=8.5, textColor=colors.HexColor('#1f2937'), alignment=0)
        td_preco = ParagraphStyle('TdPreco', fontName='Helvetica-Bold', fontSize=8.5, textColor=colors.HexColor('#1f2937'), alignment=2)

        tf_left = ParagraphStyle('TfLeft', fontName='Helvetica-Bold', fontSize=8.5, textColor=colors.HexColor('#1f2937'), alignment=0)
        tf_right = ParagraphStyle('TfRight', fontName='Helvetica-Bold', fontSize=8.5, textColor=colors.HexColor('#1f2937'), alignment=1, leading=11)

        # Montagem dos dados da tabela
        # Linha 0: Barra Superior Azul
        table_data = [
            [
                Paragraph('Coluna 1', th_col),
                Paragraph('Coluna 2', th_col),
                Paragraph(nome_estabelecimento, th_brand),
                Paragraph('Coluna 3', th_col),
                Paragraph('Coluna 4', th_col),
            ],
            # Linha 1: Subcabeçalho Cinza
            [
                Paragraph('Produto', th_sub),
                Paragraph('Preço', th_sub),
                Paragraph('', th_sub),
                Paragraph('Produto', th_sub),
                Paragraph('Preço', th_sub),
            ]
        ]

        # Linhas de Produtos lado a lado
        for item in linhas_resultado:
            table_data.append([
                Paragraph(item['produto'], td_prod),
                Paragraph(item['preco_original_fmt'], td_preco),
                Paragraph('', td_prod),
                Paragraph(item['produto'], td_prod),
                Paragraph(item['novo_preco_fmt'], td_preco),
            ])

        # Data atual por extenso para a legenda de vigência
        meses_extenso = {
            1: 'janeiro', 2: 'fevereiro', 3: 'março', 4: 'abril',
            5: 'maio', 6: 'junho', 7: 'julho', 8: 'agosto',
            9: 'setembro', 10: 'outubro', 11: 'novembro', 12: 'dezembro'
        }
        ano_atual, mes_vigente = cls._validar_mes_vigencia(mes_vigencia)
        mes_atual_nome = meses_extenso[mes_vigente]

        taxa_str = f"{kpis['taxa_ipca']:.2f}%".replace('.', ',')
        texto_rodape_dir = (
            f"Tabela de preços reajustada pelo IPCA dos últimos 12 meses ({taxa_str})"
        )

        # Linha final: Rodapé Amarelo
        table_data.append([
            Paragraph("Tabela de preços anterior", tf_left),
            Paragraph("", tf_left),
            Paragraph("", tf_left),
            Paragraph(texto_rodape_dir, tf_right),
            Paragraph("", tf_right)
        ])

        t_style = [
            # Linha 0: Top Bar Azul
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c385c')),
            ('VALIGN', (0, 0), (-1, 0), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, 0), 5),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 5),

            # Linha 1: Subcabeçalho Cinza
            ('BACKGROUND', (0, 1), (-1, 1), colors.HexColor('#a0a7b4')),
            ('VALIGN', (0, 1), (-1, 1), 'MIDDLE'),
            ('TOPPADDING', (0, 1), (-1, 1), 6),
            ('BOTTOMPADDING', (0, 1), (-1, 1), 6),

            # Bordas pretas/escuras em todas as células
            ('GRID', (0, 0), (-1, -1), 0.8, colors.HexColor('#374151')),

            # Dados
            ('VALIGN', (0, 2), (-1, -2), 'MIDDLE'),
            ('TOPPADDING', (0, 2), (-1, -2), 5),
            ('BOTTOMPADDING', (0, 2), (-1, -2), 5),
            ('BACKGROUND', (0, 2), (1, -2), colors.white),
            ('BACKGROUND', (2, 2), (2, -2), colors.HexColor('#f8fafc')),  # coluna separadora
            ('BACKGROUND', (3, 2), (4, -2), colors.white),

            # Linha Final: Rodapé Amarelo
            ('SPAN', (0, -1), (1, -1)),
            ('SPAN', (3, -1), (4, -1)),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#fde68a')),
            ('VALIGN', (0, -1), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, -1), (-1, -1), 7),
            ('BOTTOMPADDING', (0, -1), (-1, -1), 7),
        ]

        tabela = Table(table_data, colWidths=col_widths, repeatRows=2)
        tabela.setStyle(TableStyle(t_style))

        doc.build([tabela], canvasmaker=NumberedCanvas)
        buffer.seek(0)
        return buffer.getvalue()

    @classmethod
    def gerar_pdf_modelo(cls) -> bytes:
        """
        Gera um PDF modelo com tabela formatada de produtos no novo formato espelho.
        """
        dados_exemplo = [
            {'linha': 1, 'codigo': 'DOC-001', 'produto': 'Banoffe', 'categoria': 'Tortas', 'preco_original_fmt': 'R$ 10,42', 'novo_preco_fmt': 'R$ 10,90', 'diferenca_rs_fmt': '+R$ 0,48', 'diferenca_pct_fmt': '+4.61%'},
            {'linha': 2, 'codigo': 'DOC-002', 'produto': 'Mousse de Chocolate', 'categoria': 'Doces', 'preco_original_fmt': 'R$ 11,47', 'novo_preco_fmt': 'R$ 12,00', 'diferenca_rs_fmt': '+R$ 0,53', 'diferenca_pct_fmt': '+4.62%'},
            {'linha': 3, 'codigo': 'DOC-003', 'produto': 'Bolo de laranja Caseiro', 'categoria': 'Bolos', 'preco_original_fmt': 'R$ 6,25', 'novo_preco_fmt': 'R$ 6,54', 'diferenca_rs_fmt': '+R$ 0,29', 'diferenca_pct_fmt': '+4.64%'},
            {'linha': 4, 'codigo': 'DOC-004', 'produto': 'Bolo fit de Banana', 'categoria': 'Bolos', 'preco_original_fmt': 'R$ 6,25', 'novo_preco_fmt': 'R$ 6,54', 'diferenca_rs_fmt': '+R$ 0,29', 'diferenca_pct_fmt': '+4.64%'},
            {'linha': 5, 'codigo': 'DOC-005', 'produto': 'Gelatina Diet amora', 'categoria': 'Sobremesas', 'preco_original_fmt': 'R$ 5,50', 'novo_preco_fmt': 'R$ 5,75', 'diferenca_rs_fmt': '+R$ 0,25', 'diferenca_pct_fmt': '+4.55%'},
            {'linha': 6, 'codigo': 'DOC-006', 'produto': 'Sorvete Itália 120ml', 'categoria': 'Sorvetes', 'preco_original_fmt': 'R$ 6,20', 'novo_preco_fmt': 'R$ 6,48', 'diferenca_rs_fmt': '+R$ 0,28', 'diferenca_pct_fmt': '+4.52%'},
        ]
        kpis_exemplo = {
            'total_produtos': 6,
            'soma_original': 46.09,
            'soma_reajustada': 48.21,
            'aumento_total_rs': 2.12,
            'variacao_media_pct': 4.58,
            'taxa_ipca': 4.58,
            'margem_adicional': 0.0,
            'descricao_indice': 'IPCA acumulado nos últimos 12 meses: +4.58%',
            'regra_arredondamento': 'padrao'
        }
        return cls.gerar_pdf_reajustado(dados_exemplo, kpis_exemplo)

    @classmethod
    def _gerar_excel_formatado(
        cls,
        df: pd.DataFrame,
        coluna_preco_orig: str,
        taxa_ipca: float,
        margem_adicional: float,
        descricao_indice: str
    ) -> bytes:
        """
        Gera uma planilha Excel .xlsx estilizada.
        """
        output = io.BytesIO()

        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Produtos Reajustados', index=False, startrow=3)
            ws = writer.sheets['Produtos Reajustados']

            ws.merge_cells('A1:F1')
            titulo_cell = ws['A1']
            titulo_cell.value = f"REAJUSTE DE PREÇOS — ÍNDICE IPCA (BANCO CENTRAL) | {datetime.now().strftime('%d/%m/%Y')}"
            titulo_cell.font = Font(name='Calibri', size=14, bold=True, color='FFFFFF')
            titulo_cell.fill = PatternFill(start_color='1F2937', end_color='1F2937', fill_type='solid')
            titulo_cell.alignment = Alignment(horizontal='left', vertical='center')

            ws.merge_cells('A2:F2')
            sub_cell = ws['A2']
            sub_cell.value = f"{descricao_indice} | Margem extra: {margem_adicional:+.1f}% | Gerado por Doce como Mel Gestão"
            sub_cell.font = Font(name='Calibri', size=10, italic=True, color='D1D5DB')
            sub_cell.fill = PatternFill(start_color='374151', end_color='374151', fill_type='solid')
            sub_cell.alignment = Alignment(horizontal='left', vertical='center')

            header_fill = PatternFill(start_color='D97706', end_color='D97706', fill_type='solid')
            header_destaque_fill = PatternFill(start_color='059669', end_color='059669', fill_type='solid')
            header_font = Font(name='Calibri', size=11, bold=True, color='FFFFFF')
            thin_border = Border(
                left=Side(style='thin', color='E5E7EB'),
                right=Side(style='thin', color='E5E7EB'),
                top=Side(style='thin', color='E5E7EB'),
                bottom=Side(style='thin', color='E5E7EB')
            )

            col_novo_preco_idx = None
            for col_idx, col_name in enumerate(df.columns, 1):
                cell = ws.cell(row=4, column=col_idx)
                cell.font = header_font
                if col_name == 'Preço Reajustado (IPCA)':
                    cell.fill = header_destaque_fill
                    col_novo_preco_idx = col_idx
                else:
                    cell.fill = header_fill
                cell.alignment = Alignment(horizontal='center', vertical='center')

            zebra_fill = PatternFill(start_color='F9FAFB', end_color='F9FAFB', fill_type='solid')
            destaque_col_fill = PatternFill(start_color='ECFDF5', end_color='ECFDF5', fill_type='solid')

            total_linhas = len(df)
            for row_idx in range(5, 5 + total_linhas):
                is_zebra = (row_idx % 2 == 0)
                for col_idx, col_name in enumerate(df.columns, 1):
                    c = ws.cell(row=row_idx, column=col_idx)
                    c.border = thin_border
                    if is_zebra and col_idx != col_novo_preco_idx:
                        c.fill = zebra_fill

                    if col_name in (coluna_preco_orig, 'Preço Reajustado (IPCA)', 'Aumento (R$)'):
                        c.number_format = 'R$ #,##0.00'
                        if col_idx == col_novo_preco_idx:
                            c.fill = destaque_col_fill
                            c.font = Font(bold=True, color='065F46')
                    elif col_name == 'Variação (%)':
                        c.number_format = '0.00"%"'
                        c.font = Font(color='047857')

            for col in ws.columns:
                max_len = 0
                col_letter = get_column_letter(col[0].column)
                for cell in col:
                    if cell.row in (1, 2):
                        continue
                    val_str = str(cell.value or '')
                    if len(val_str) > max_len:
                        max_len = len(val_str)
                ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

        output.seek(0)
        return output.getvalue()

    @classmethod
    def gerar_planilha_modelo(cls) -> bytes:
        """
        Gera uma planilha modelo (.xlsx) com produtos fictícios.
        """
        dados = [
            {'Código': 'DOC-001', 'Produto': 'Banoffe', 'Categoria': 'Tortas', 'Preço': 10.42, 'Unidade': 'Fatia', 'Estoque': 20},
            {'Código': 'DOC-002', 'Produto': 'Mousse de Chocolate', 'Categoria': 'Doces', 'Preço': 11.47, 'Unidade': 'Taça', 'Estoque': 15},
            {'Código': 'DOC-003', 'Produto': 'Bolo de laranja Caseiro', 'Categoria': 'Bolos', 'Preço': 6.25, 'Unidade': 'Fatia', 'Estoque': 30},
            {'Código': 'DOC-004', 'Produto': 'Bolo fit de Banana', 'Categoria': 'Bolos', 'Preço': 6.25, 'Unidade': 'Fatia', 'Estoque': 25},
            {'Código': 'DOC-005', 'Produto': 'Gelatina Diet amora', 'Categoria': 'Sobremesas', 'Preço': 5.50, 'Unidade': 'Pote', 'Estoque': 40},
            {'Código': 'DOC-006', 'Produto': 'Sorvete Itália 120ml', 'Categoria': 'Sorvetes', 'Preço': 6.20, 'Unidade': 'Pote', 'Estoque': 50},
        ]
        df = pd.DataFrame(dados)

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Tabela de Produtos', index=False, startrow=2)
            ws = writer.sheets['Tabela de Produtos']

            ws.merge_cells('A1:F1')
            b = ws['A1']
            b.value = "MODELO DE TABELA DE PRODUTOS — Preencha com seus itens e envie para reajuste pelo IPCA"
            b.font = Font(name='Calibri', size=11, bold=True, color='FFFFFF')
            b.fill = PatternFill(start_color='D97706', end_color='D97706', fill_type='solid')
            b.alignment = Alignment(horizontal='center', vertical='center')

            header_fill = PatternFill(start_color='1F2937', end_color='1F2937', fill_type='solid')
            header_font = Font(name='Calibri', size=11, bold=True, color='FFFFFF')

            for col_idx, _ in enumerate(df.columns, 1):
                c = ws.cell(row=3, column=col_idx)
                c.fill = header_fill
                c.font = header_font
                c.alignment = Alignment(horizontal='center', vertical='center')

            for row_idx in range(4, 4 + len(df)):
                cell_preco = ws.cell(row=row_idx, column=4)
                cell_preco.number_format = 'R$ #,##0.00'

            for col in ws.columns:
                max_len = 0
                col_letter = get_column_letter(col[0].column)
                for cell in col:
                    if cell.row == 1:
                        continue
                    val_str = str(cell.value or '')
                    if len(val_str) > max_len:
                        max_len = len(val_str)
                ws.column_dimensions[col_letter].width = max(max_len + 4, 14)

        output.seek(0)
        return output.getvalue()