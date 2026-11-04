"""
extrator_nota.py — Extrator de notas fiscais, boletos e comprovantes em PDF e imagens
sem dependência de IA ou GPU, utilizando pdfplumber, RapidOCR e expressões regulares.

Projetado para rodar em hardware modesto (ex: Intel Celeron, 4GB RAM).

Retorna um dicionário com:
{
    'fornecedor': str,
    'data': str (AAAA-MM-DD),
    'forma_pagamento': str,
    'parcelas': int,
    'valor_total': float,
    'itens': list[dict]
}
"""

import io
import re
import logging
import time

from datetime import datetime
from typing import Any

import pdfplumber


logger = logging.getLogger(__name__)


# ============================================================
# RAPIDOCR COMPARTILHADO
# ============================================================

def _obter_ocr_engine():
    """Obtém a instância compartilhada do RapidOCR."""
    from app.services.planilha_produto_service import (
        _obter_ocr_engine as obter
    )

    return obter()


def _executar_ocr(imagem):
    """Executa o OCR utilizando o bloqueio compartilhado."""
    from app.services.planilha_produto_service import (
        _executar_ocr as executar
    )

    return executar(imagem)


# ============================================================
# OCR DE IMAGENS
# ============================================================

def _imagem_bytes_para_texto_ocr(image_bytes: bytes) -> str:
    """
    Recebe bytes de uma imagem e retorna o texto extraído
    via RapidOCR, registrando o tempo de cada etapa.
    """

    import numpy as np
    import os

    from PIL import Image, ImageOps

    inicio_total = time.perf_counter()

    logger.info(
        "OCR | Iniciando processamento | Tamanho: %.2f MB",
        len(image_bytes) / (1024 * 1024)
    )

    # ETAPA 1: Obter o motor OCR
    inicio = time.perf_counter()

    try:
        _obter_ocr_engine()

    except Exception:
        logger.exception("OCR | Erro ao obter/inicializar o motor")
        raise

    tempo_motor = time.perf_counter() - inicio

    logger.info(
        "OCR | Motor obtido em %.2f segundos",
        tempo_motor
    )

    # ETAPA 2: Abrir e preparar a imagem
    inicio = time.perf_counter()

    try:
        with Image.open(io.BytesIO(image_bytes)) as imagem_original:
            img = ImageOps.exif_transpose(imagem_original).convert("RGB")

        largura_original, altura_original = img.size

        lado_max = int(os.getenv("OCR_MAX_LADO", "1600"))
        alvo = int(os.getenv("OCR_ALVO", "1600"))

        # Limita o tamanho máximo da imagem
        img.thumbnail((lado_max, lado_max))

        # Amplia imagens pequenas somente quando necessário
        fator = min(3, max(1, round(alvo / max(img.size))))

        if fator > 1:
            img = img.resize(
                (img.width * fator, img.height * fator),
                Image.LANCZOS
            )

        largura_final, altura_final = img.size

        imagem_np = np.array(img)

        logger.info(
            "OCR | Imagem preparada | Original: %sx%s | Final: %sx%s",
            largura_original,
            altura_original,
            largura_final,
            altura_final
        )

    except Exception:
        logger.exception("OCR | Erro ao preparar a imagem")
        raise

    tempo_preparo = time.perf_counter() - inicio

    logger.info(
        "OCR | Preparação concluída em %.2f segundos",
        tempo_preparo
    )

    # ETAPA 3: Executar o RapidOCR
    inicio = time.perf_counter()

    try:
        logger.info(
            "OCR | Aguardando execução compartilhada do RapidOCR"
        )

        res, _ = _executar_ocr(imagem_np)

        tempo_inferencia = time.perf_counter() - inicio

        quantidade = len(res) if res else 0

        logger.info(
            "OCR | Inferência concluída em %.2f segundos | "
            "Caixas detectadas: %d",
            tempo_inferencia,
            quantidade
        )

    except Exception:
        logger.exception("OCR | Erro durante a inferência")
        raise

    if not res:
        logger.warning("OCR | Nenhum texto detectado")

        logger.info(
            "OCR | Processamento total: %.2f segundos",
            time.perf_counter() - inicio_total
        )

        return ""

    # ETAPA 4: Organizar os textos por linha
    inicio = time.perf_counter()

    caixas = []

    for box, texto, *_ in res:
        texto = str(texto).strip()

        if not texto:
            continue

        ys = [p[1] for p in box]
        xs = [p[0] for p in box]

        caixas.append({
            "texto": texto,
            "y": (min(ys) + max(ys)) / 2.0,
            "x": min(xs),
            "h": max(ys) - min(ys)
        })

    if not caixas:
        logger.warning("OCR | Nenhuma caixa de texto utilizável")

        logger.info(
            "OCR | Processamento total: %.2f segundos",
            time.perf_counter() - inicio_total
        )

        return ""

    altura_mediana = sorted(
        c["h"] for c in caixas
    )[len(caixas) // 2]

    tolerancia = max(4.0, 0.6 * altura_mediana)

    caixas.sort(key=lambda c: c["y"])

    linhas_agrupadas = []

    for c in caixas:
        if (
            linhas_agrupadas
            and abs(linhas_agrupadas[-1]["y"] - c["y"]) <= tolerancia
        ):
            linhas_agrupadas[-1]["itens"].append(c)

            linhas_agrupadas[-1]["y"] += (
                c["y"] - linhas_agrupadas[-1]["y"]
            ) / len(linhas_agrupadas[-1]["itens"])

        else:
            linhas_agrupadas.append({
                "y": c["y"],
                "itens": [c]
            })

    linhas_texto = []

    for linha in linhas_agrupadas:
        texto_linha = " ".join(
            i["texto"]
            for i in sorted(
                linha["itens"],
                key=lambda i: i["x"]
            )
        )

        linhas_texto.append(texto_linha.strip())

    texto_final = "\n".join(linhas_texto)

    tempo_organizacao = time.perf_counter() - inicio
    tempo_total = time.perf_counter() - inicio_total

    logger.info(
        "OCR | Organização concluída em %.2f segundos",
        tempo_organizacao
    )

    logger.info(
        "OCR | Finalizado | Caracteres: %d | Tempo total: %.2f segundos",
        len(texto_final),
        tempo_total
    )

    return texto_final


# ============================================================
# EXTRAÇÃO DE DADOS DE IMAGENS
# ============================================================

def extrair_dados_imagem_ocr(image_bytes: bytes) -> dict[str, Any]:
    """
    Extrai dados de nota fiscal / comprovante a partir de uma imagem
    (JPEG, PNG, WEBP), usando RapidOCR e os mesmos regex de extrair_dados_pdf.

    Retorna o mesmo formato de dicionário que extrair_dados_pdf.
    """

    texto_completo = _imagem_bytes_para_texto_ocr(image_bytes)

    if not texto_completo.strip():
        logger.warning(
            "OCR não detectou texto utilizável na imagem."
        )

        return {
            "fornecedor": "",
            "data": "",
            "forma_pagamento": "",
            "parcelas": 1,
            "valor_total": 0.0,
            "itens": [],
        }

    logger.info(
        "OCR extraiu %d caracteres da imagem.",
        len(texto_completo)
    )

    # Reutiliza o pipeline de regex
    return _extrair_de_texto(texto_completo)


# ============================================================
# EXTRAÇÃO DE DADOS DE PDF
# ============================================================

def extrair_dados_pdf(
    file_source: bytes | io.BytesIO | str
) -> dict[str, Any]:
    """
    Lê o texto e tabelas de um documento PDF e devolve os dados
    estruturados da nota fiscal/comprovante.
    """

    if isinstance(file_source, bytes):
        stream = io.BytesIO(file_source)

    elif isinstance(file_source, io.BytesIO):
        stream = file_source

    elif isinstance(file_source, str):
        stream = file_source

    else:
        raise ValueError(
            f"Fonte de arquivo inválida: {type(file_source)}"
        )

    texto_completo = ""
    linhas = []

    with pdfplumber.open(stream) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""

            texto_completo += t + "\n"

            for line in t.splitlines():
                line_str = line.strip()

                if line_str:
                    linhas.append(line_str)

    if not texto_completo.strip():
        logger.warning(
            "Nenhum texto pôde ser extraído do PDF "
            "(pode ser imagem digitalizada sem camada de texto)."
        )

        return {
            'fornecedor': '',
            'data': '',
            'forma_pagamento': '',
            'parcelas': 1,
            'valor_total': 0.0,
            'itens': []
        }

    return _extrair_de_texto(texto_completo, linhas)


# ============================================================
# PIPELINE PRINCIPAL DE EXTRAÇÃO POR EXPRESSÕES REGULARES
# ============================================================

def _extrair_de_texto(
    texto_completo: str,
    linhas: list[str] | None = None
) -> dict[str, Any]:
    """
    Pipeline principal de extração por regex.

    Recebe o texto bruto (de pdfplumber ou de OCR) e retorna
    o dicionário padronizado.

    Se 'linhas' não for fornecida, é gerada a partir de 'texto_completo'.
    """

    if linhas is None:
        linhas = [
            l.strip()
            for l in texto_completo.splitlines()
            if l.strip()
        ]

    # ========================================================
    # 1. IDENTIFICAR FORNECEDOR / ESTABELECIMENTO / BENEFICIÁRIO
    # ========================================================

    fornecedor = ""

    # Casos DANFE / NF-e:
    # "RECEBEMOS DE <NOME>" ou "EMITENTE: <NOME>" ou "RAZÃO SOCIAL"

    m_rec = re.search(
        r"RECEBEMOS\s+DE\s+([^\n\r,]+)",
        texto_completo,
        re.IGNORECASE
    )

    if m_rec:
        fornecedor = m_rec.group(1).strip()

    elif (
        "Beneficiário" in texto_completo
        or "Beneficirio" in texto_completo
    ):
        for i, l in enumerate(linhas):

            if re.search(
                r"Benefici[aá\uFFFD]rio",
                l,
                re.IGNORECASE
            ):
                if i + 1 < len(linhas):
                    prox = linhas[i + 1]

                    # Limpa CNPJ/CPF se vier na mesma linha
                    prox_clean = re.sub(
                        r"\d{2}\.?\d{3}\.?\d{3}/\d{4}-\d{2}|\d{11,14}",
                        "",
                        prox
                    ).strip()

                    fornecedor = prox_clean.split("  ")[0].strip()
                    break

    if not fornecedor:

        m_emit = re.search(
            r"(?:EMITENTE|RAZ[AÃ\uFFFD]O\s+SOCIAL|FORNECEDOR)[:\s]+([^\n\r]+)",
            texto_completo,
            re.IGNORECASE
        )

        if m_emit:
            fornecedor = m_emit.group(1).strip()

        else:
            # Fallback: pega a primeira linha informativa
            # que não seja cabeçalho genérico

            for l in linhas[:5]:

                if not re.search(
                    r"INFORMATIVO|DANFE|DOCUMENTO|NOTA FISCAL|RECIBO|COMPROVANTE|PREFEITURA",
                    l,
                    re.IGNORECASE
                ) and len(l) > 3:

                    fornecedor = l
                    break

    # ========================================================
    # 2. IDENTIFICAR DATA
    # ========================================================

    data_formatada = ""

    # Padrão DD/MM/AAAA, DD-MM-AAAA ou DD.MM.AAAA

    datas_encontradas = re.findall(
        r"\b(\d{2})[/.-](\d{2})[/.-](\d{4})\b",
        texto_completo
    )

    if datas_encontradas:

        for d, m, a in datas_encontradas:

            try:
                dt = datetime(
                    int(a),
                    int(m),
                    int(d)
                )

                data_formatada = dt.strftime("%Y-%m-%d")
                break

            except ValueError:
                continue

    # ========================================================
    # 3. IDENTIFICAR FORMA DE PAGAMENTO E PARCELAS
    # ========================================================

    forma_pagamento = "Boleto / Pix"
    parcelas = 1

    t_upper = texto_completo.upper()

    if "PIX" in t_upper:
        forma_pagamento = "Pix"

    elif (
        "CARTAO DE CREDITO" in t_upper
        or "CARTÃO DE CRÉDITO" in t_upper
        or "CRÉDITO" in t_upper
    ):
        forma_pagamento = "Cartão de Crédito"

    elif (
        "CARTAO DE DEBITO" in t_upper
        or "CARTÃO DE DÉBITO" in t_upper
        or "DÉBITO" in t_upper
    ):
        forma_pagamento = "Cartão de Débito"

    elif "DINHEIRO" in t_upper:
        forma_pagamento = "Dinheiro"

    elif (
        "TRANSFERENCIA" in t_upper
        or "TRANSFERÊNCIA" in t_upper
        or "TED" in t_upper
    ):
        forma_pagamento = "Transferência"

    # Detecção de parcelas:
    # Exemplo: 3x, 3 parcelas, 1/4

    m_parc = re.search(
        r"(\d{1,2})\s*X\b|PARCELA\s*(\d{1,2})|PARC\.?\s*(\d{1,2})[/de\s]+(\d{1,2})",
        t_upper
    )

    if m_parc:

        grupos = [
            g for g in m_parc.groups()
            if g
        ]

        if grupos:

            try:
                num = int(grupos[-1])

                if 1 <= num <= 48:
                    parcelas = num

                    if (
                        forma_pagamento != "Cartão de Crédito"
                        and "CART" in t_upper
                    ):
                        forma_pagamento = "Cartão de Crédito"

            except (ValueError, TypeError):
                pass

    # ========================================================
    # 4. IDENTIFICAR VALOR TOTAL
    # ========================================================

    valor_total = 0.0

    # Procura por:
    # VALOR DO DOCUMENTO
    # VALOR TOTAL
    # TOTAL DA NOTA
    # VALOR COBRADO

    padroes_total = [

        r"(?:VALOR\s+DO\s+DOCUMENTO|VALOR\s+COBRADO|TOTAL\s+DA\s+NOTA|VALOR\s+TOTAL|TOTAL\s+A\s+PAGAR)[\s:=]*R?\$?\s*([\d\.,]+)",

        r"(?:VALOR|TOTAL)[\s:=]+R?\$?\s*([\d\.,]+)",

        r"R\$\s*([\d\.,]+)"
    ]

    for padrao in padroes_total:

        matches = re.findall(
            padrao,
            texto_completo,
            re.IGNORECASE
        )

        candidatos = []

        for match in matches:

            v_str = match.strip().replace(" ", "")

            # Trata formato brasileiro 1.234,56 ou 1234.56

            if "," in v_str and "." in v_str:

                if v_str.rfind(",") > v_str.rfind("."):
                    v_str = v_str.replace(".", "").replace(",", ".")

                else:
                    v_str = v_str.replace(",", "")

            elif "," in v_str:
                v_str = v_str.replace(",", ".")

            try:
                val = float(v_str)

                if val > 0:
                    candidatos.append(val)

            except ValueError:
                continue

        if candidatos:

            # Em boletos e DANFEs, o valor do documento
            # geralmente é o maior ou o primeiro explícito

            valor_total = candidatos[0]
            break

    # ========================================================
    # 5. IDENTIFICAR ITENS
    # ========================================================

    itens = []

    # Tenta localizar linhas de produtos em DANFE ou lista simples

    palavras_bloqueadas = [

        "VALOR",
        "TOTAL",
        "DOCUMENTO",
        "MULTA",
        "JUROS",
        "DESCONTO",
        "PAGADOR",
        "BENEFICIARIO",
        "BENEFICIÁRIO",
        "AGÊNCIA",
        "AGENCIA",
        "CÓDIGO",
        "CODIGO",
        "CEDENTE",
        "SACADO",
        "COMPENSAÇÃO",
        "COMPENSACAO",
        "CNPJ",
        "CPF",
        "ENDEREÇO",
        "ENDERECO",
        "REAL"
    ]

    for l in linhas:

        # Linhas contendo descrição e valor com R$

        m_item = re.search(
            r"^(.*?)\s+R\$\s*([\d\.,]+)$",
            l
        )

        if not m_item and " R$ " in l:

            partes = l.split(" R$ ")

            if len(partes) == 2:

                m_item = re.search(
                    r"^(.*?)\s+([\d\.,]+)$",
                    f"{partes[0]} {partes[1]}"
                )

        if m_item:

            desc = m_item.group(1).strip()

            # Ignora se for linha de boleto bancário ou cabeçalho

            if any(
                kw in desc.upper()
                for kw in palavras_bloqueadas
            ):
                continue

            v_txt = m_item.group(2).strip()

            if "," in v_txt and "." in v_txt:
                v_txt = v_txt.replace(".", "").replace(",", ".")

            elif "," in v_txt:
                v_txt = v_txt.replace(",", ".")

            try:
                v_float = float(v_txt)

                # Valores de itens não podem ser gigantescos
                # nem exceder o total

                if (
                    0.01 <= v_float <= (
                        valor_total if valor_total > 0 else 999999
                    )
                    and len(desc) > 2
                ):

                    itens.append({
                        'descricao': desc,
                        'valor': v_float,
                        'categoria_sugerida': ''
                    })

            except ValueError:
                pass

    if not itens and valor_total > 0:

        desc_padrao = fornecedor or "Despesa de Comprovante"

        itens.append({
            'descricao': desc_padrao,
            'valor': valor_total,
            'categoria_sugerida': ''
        })

    # ========================================================
    # RETORNO PADRONIZADO
    # ========================================================

    return {
        'fornecedor': fornecedor,
        'data': data_formatada,
        'forma_pagamento': forma_pagamento,
        'parcelas': parcelas,
        'valor_total': valor_total,
        'itens': itens
    }
