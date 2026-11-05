import base64
import json
import logging
import os
import re
from datetime import date
from io import BytesIO
from typing import Any

import requests
from PIL import Image, ImageOps  # pip install pillow

logger = logging.getLogger(__name__)


class AiFiscalService:
    """Leitura de notas fiscais e comprovantes com Ollama + Qwen3-VL (local)."""

    OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    # Variante "instruct" não faz raciocínio (<think>), logo é mais rápida.
    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3-vl:4b-instruct")
    TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "180"))
    NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
    NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "2048"))
    KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")
    IMG_LADO_MAX = int(os.getenv("OLLAMA_IMG_MAX", "1280"))
    MAX_PAGINAS_PDF = 5

    TIPOS_TRANSACAO = ("despesa", "receita")
    TIPOS_RECEITA = ("Venda", "Encomenda", "Serviço", "Delivery", "Feira/Evento", "Outros")
    FORMAS_PAGAMENTO = ("Pix", "Dinheiro", "Cartão de Débito", "Cartão de Crédito", "Transferência")

    SCHEMA = {
        "type": "object",
        "properties": {
            "tipo_transacao": {"type": "string", "enum": ["despesa", "receita"]},
            "data": {"type": "string"},
            "estabelecimento": {"type": "string"},
            "forma_pagamento": {"type": "string"},
            "tipo_receita": {"type": "string"},
            "valor_total": {"type": "number"},
            "is_parcelado": {"type": "boolean"},
            "numero_parcelas": {"type": "integer"},
            "valor_parcela": {"type": "number"},
            "itens": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "descricao": {"type": "string"},
                        "valor": {"type": "number"},
                        "categoria_sugerida": {"type": "string"},
                    },
                    "required": ["descricao", "valor"],
                },
            },
            "observacoes": {"type": "string"},
        },
        "required": [
            "tipo_transacao",
            "data",
            "estabelecimento",
            "valor_total",
            "itens",
            "observacoes",
        ],
    }

    # ------------------------------------------------------------------
    # Disponibilidade
    # ------------------------------------------------------------------
    @classmethod
    def get_provider(cls) -> str:
        return os.getenv("AI_PROVIDER", "texto").strip().lower()

    @classmethod
    def is_configurado(cls) -> bool:
        import time as _t
        agora = _t.time()
        cache = getattr(cls, "_cfg_cache", None)
        if cache and agora - cache[0] < 120:
            return cache[1]
        ok = cls._verificar_configurado()
        cls._cfg_cache = (agora, ok)
        return ok

    @classmethod
    def _verificar_configurado(cls) -> bool:
        provider = cls.get_provider()
        if provider == "texto":
            return True
        try:
            response = requests.get(f"{cls.OLLAMA_HOST}/api/tags", timeout=2)
            response.raise_for_status()

            nomes = {m.get("name", "") for m in response.json().get("models", [])}
            candidatos = {cls.OLLAMA_MODEL}
            if ":" not in cls.OLLAMA_MODEL:
                candidatos.add(f"{cls.OLLAMA_MODEL}:latest")

            if nomes & candidatos:
                return True

            logger.warning("Modelo '%s' não encontrado no Ollama.", cls.OLLAMA_MODEL)
            return False
        except requests.RequestException as e:
            logger.warning("Ollama não está disponível: %s", e)
            return False
        except Exception as e:
            logger.warning("Erro ao verificar Ollama: %s", e)
            return False

    # ------------------------------------------------------------------
    # Interpretação
    # ------------------------------------------------------------------
    @classmethod
    def interpretar_documento(
        cls,
        file_bytes: bytes,
        mimetype: str,
        categorias_existentes: list[str],
    ) -> dict[str, Any]:
        hoje_str = date.today().strftime("%Y-%m-%d")
        provider = cls.get_provider()

        if provider == "texto":
            return cls._processar_com_extrator_texto(file_bytes, mimetype, hoje_str, categorias_existentes)
        elif provider == "ollama":
            if cls.is_configurado():
                return cls._processar_com_ollama(file_bytes, mimetype, hoje_str, categorias_existentes)
            logger.warning("Ollama indisponivel. Usando OCR (extrator de texto) como alternativa.")
            return cls._processar_com_extrator_texto(file_bytes, mimetype, hoje_str, categorias_existentes)
        else:
            logger.warning("Provedor '%s' desconhecido. Utilizando 'texto' como padrão.", provider)
            return cls._processar_com_extrator_texto(file_bytes, mimetype, hoje_str, categorias_existentes)

    @classmethod
    def _processar_com_extrator_texto(
        cls,
        file_bytes: bytes,
        mimetype: str,
        hoje_str: str,
        categorias_existentes: list[str],
    ) -> dict[str, Any]:
        """
        Processa documentos PDF diretamente via extrator_nota.py sem IA/GPU.
        Se falhar ou não conseguir identificar valores, retorna estrutura vazia para preenchimento manual.
        """
        import time as _time
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutTimeout

        TIMEOUT_S = int(os.getenv("EXTRACAO_TIMEOUT", "120"))  # 2 min padrão
        _t0 = _time.perf_counter()

        try:
            import extrator_nota

            logger.info(
                "EXTRATOR | início | MIME: %s | Bytes: %d | Timeout: %ds",
                mimetype, len(file_bytes), TIMEOUT_S
            )

            def _chamar_extrator():
                if mimetype.endswith("pdf"):
                    return extrator_nota.extrair_dados_pdf(file_bytes)
                elif mimetype.startswith("image/") and os.getenv("OCR_IMAGEM", "1") == "1":
                    logger.info("EXTRATOR | imagem recebida (%s). Iniciando OCR.", mimetype)
                    return extrator_nota.extrair_dados_imagem_ocr(file_bytes)
                return None  # tipo não suportado — sinaliza abaixo

            if not (mimetype.endswith("pdf") or
                    (mimetype.startswith("image/") and os.getenv("OCR_IMAGEM", "1") == "1")):
                logger.info("EXTRATOR | tipo não suportado: %s", mimetype)
                return cls._gerar_resultado_manual(
                    f"Formato '{mimetype}' não suportado pelo extrator. Preencha os campos manualmente.",
                    hoje_str,
                )

            # Roda em thread separada para permitir timeout sem bloquear o worker
            with ThreadPoolExecutor(max_workers=1) as _ex:
                _fut = _ex.submit(_chamar_extrator)
                try:
                    dados_extrator = _fut.result(timeout=TIMEOUT_S)
                except _FutTimeout:
                    logger.error(
                        "EXTRATOR | TIMEOUT após %ds | MIME: %s | Bytes: %d",
                        TIMEOUT_S, mimetype, len(file_bytes)
                    )
                    return cls._gerar_resultado_manual(
                        f"Tempo limite de extração excedido ({TIMEOUT_S}s). Preencha os dados manualmente.",
                        hoje_str,
                    )

            if dados_extrator is None:
                return cls._gerar_resultado_manual(
                    f"Formato '{mimetype}' não suportado pelo extrator. Preencha os campos manualmente.",
                    hoje_str,
                )

            logger.info(
                "EXTRATOR | extração concluída em %.1fs | fornecedor=%r | valor=%.2f | parcelas=%d",
                _time.perf_counter() - _t0,
                dados_extrator.get("fornecedor", ""),
                float(dados_extrator.get("valor_total") or 0),
                int(dados_extrator.get("parcelas") or 1),
            )

            # Converte o retorno do extrator_nota para o formato esperado pela tela de revisão
            dados_convertidos = cls._converter_extrator_para_revisao(dados_extrator, hoje_str, categorias_existentes)

            logger.info(
                "EXTRATOR | conversão concluída | forma_pagamento=%r | total=%.2f",
                dados_convertidos.get("forma_pagamento"),
                dados_convertidos.get("valor_total", 0),
            )

            if dados_convertidos["valor_total"] == 0.0 and not dados_convertidos["itens"]:
                return cls._gerar_resultado_manual(
                    "Não foi possível identificar os dados automaticamente no documento. Por favor, preencha manualmente.",
                    hoje_str,
                )

            dados_convertidos["ia_sucesso"] = True
            dados_convertidos["modo_demo"] = False
            return dados_convertidos

        except Exception as e:
            # Loga com traceback completo, sem engolir exceções
            logger.exception(
                "EXTRATOR | ERRO após %.1fs | MIME: %s | %s",
                _time.perf_counter() - _t0, mimetype, e
            )
            return cls._gerar_resultado_manual(
                f"Erro na extração automática ({e}). Por favor, preencha os dados manualmente.",
                hoje_str,
            )


    @classmethod
    def _converter_extrator_para_revisao(
        cls,
        d: dict[str, Any],
        hoje_str: str,
        categorias_existentes: list[str]
    ) -> dict[str, Any]:
        """
        Converte o dicionário {fornecedor, data, forma_pagamento, parcelas, valor_total, itens}
        para a estrutura que a tela de revisão e o banco utilizam.
        """
        fornecedor = str(d.get("fornecedor") or "").strip()
        data_str = str(d.get("data") or "").strip()
        if not data_str:
            data_str = hoje_str

        fp = str(d.get("forma_pagamento") or "").strip()
        if fp not in cls.FORMAS_PAGAMENTO:
            _fpu = fp.upper()
            if re.search(r"CART[AÃ]O\s+(?:DE\s+)?CR[EÉ]DITO|CR[EÉ]DITO.*\d+\s*[xX]|CREDITO\s+PARC", _fpu):
                fp = "Cartão de Crédito"
            elif re.search(r"CART[AÃ]O\s+(?:DE\s+)?D[EÉ]BITO|COMPRA\s+D[EÉ]BITO", _fpu):
                fp = "Cartão de Débito"
            elif re.search(r"\bPIX\b|CHAVE\s+PIX", _fpu):
                fp = "Pix"
            elif re.search(r"\bDINHEIRO\b|\bESP[EÉ]CIE\b", _fpu):
                fp = "Dinheiro"
            elif re.search(r"\bTRANSFER[EÊ]NCIA\b|\bTED\b|\bDOC\b|\bTEF\b", _fpu):
                fp = "Transferência"
            elif re.search(r"\bBOLETO\b", _fpu):
                fp = "Pix"
            else:
                fp = ""  # Deixa vazio; usuário preenche na revisão

        parcelas = int(d.get("parcelas") or 1)
        is_parcelado = parcelas > 1
        valor_total = round(float(d.get("valor_total") or 0.0), 2)
        valor_parcela = round(valor_total / parcelas, 2) if is_parcelado and parcelas > 0 else 0.0

        itens_in = d.get("itens") or []
        itens_out = []
        cat_padrao = categorias_existentes[0] if categorias_existentes else "Outros"

        for item in itens_in:
            if isinstance(item, dict):
                desc = str(item.get("descricao") or "").strip()
                val = round(float(item.get("valor") or 0.0), 2)
                cat = str(item.get("categoria_sugerida") or "").strip()
                if not cat:
                    cat = cat_padrao
                if desc or val > 0:
                    itens_out.append({
                        "descricao": desc or fornecedor or "Item",
                        "valor": val,
                        "categoria_sugerida": cat
                    })

        if not itens_out and valor_total > 0:
            itens_out.append({
                "descricao": fornecedor or "Despesa de Comprovante",
                "valor": valor_total,
                "categoria_sugerida": cat_padrao
            })

        return {
            "tipo_transacao": "despesa",
            "data": cls._normalizar_data(data_str, hoje_str),
            "estabelecimento": fornecedor,
            "forma_pagamento": fp,
            "tipo_receita": "Outros",
            "valor_total": valor_total,
            "is_parcelado": is_parcelado,
            "numero_parcelas": parcelas,
            "valor_parcela": valor_parcela,
            "itens": itens_out,
            "observacoes": f"Extraído via Extrator de Texto (sem IA). Fornecedor: {fornecedor}" if fornecedor else "Extraído via Extrator de Texto (sem IA).",
        }

    @classmethod
    def _processar_com_ollama(
        cls,
        file_bytes: bytes,
        mimetype: str,
        hoje_str: str,
        categorias_existentes: list[str],
    ) -> dict[str, Any]:
        categorias_str = (
            ", ".join(categorias_existentes)
            if categorias_existentes
            else "Ingredientes, Embalagens, Operacional, Outros"
        )

        prompt = f"""Leia o documento fiscal da imagem (nota fiscal, cupom, recibo ou comprovante) e responda somente com o JSON dos dados extraídos. Não explique nada.

Transcreva exatamente o que estiver visível, mesmo que os valores pareçam fictícios ou de teste.

Campos:
- tipo_transacao: "despesa" (compra de insumos, embalagens ou gasto operacional) ou "receita" (venda ou recebimento de cliente).
  Em NF-e/DANFE com "RECEBEMOS DE <empresa>", a empresa citada é o emissor/fornecedor: é uma compra (despesa) e o estabelecimento é o emissor.
- data: AAAA-MM-DD. Se não estiver visível, use "{hoje_str}".
- estabelecimento: nome do emissor, fornecedor ou cliente.
- forma_pagamento: uma entre {", ".join(cls.FORMAS_PAGAMENTO)}. Se no documento houver menção a cartão ou parcelamento, defina como "Cartão de Crédito". Se não visível, deixe vazio.
- tipo_receita: uma entre {", ".join(cls.TIPOS_RECEITA)}. Use "Outros" se não souber.
- valor_total: total pago/cobrado, como número (ex.: 85.50).
- is_parcelado: true se a compra foi parcelada (ex.: "em 3x", "parc. 1/6", "3 vezes", "3x de"), caso contrário false.
- numero_parcelas: número total de parcelas se parcelado (ex.: 2, 3, 6, 10, 12). Se for à vista ou não informado, use 1.
- valor_parcela: valor de cada parcela se informado explicitamente no comprovante, ou 0.
- itens: lista com descricao, valor (total do item, não o unitário) e categoria_sugerida.
- observacoes: uma frase curta sobre a compra. Não copie rodapés, avisos ou propagandas do documento.

Categorias existentes: {categorias_str}
Use sempre uma categoria existente quando houver correspondência.
Se houver apenas o total sem itens, crie um único item com o valor total.
Não invente dados que não estejam no documento."""

        try:
            imagens = cls._preparar_imagens(file_bytes, mimetype)
            if not imagens:
                raise ValueError("Não foi possível converter o documento em imagem.")

            imagens_base64 = [base64.b64encode(i).decode("utf-8") for i in imagens]

            payload = {
                "model": cls.OLLAMA_MODEL,
                "messages": [
                    {"role": "user", "content": prompt, "images": imagens_base64}
                ],
                "stream": False,
                "think": False,
                "format": cls.SCHEMA,
                "keep_alive": cls.KEEP_ALIVE,
                "options": {
                    "temperature": 0,
                    "num_ctx": cls.NUM_CTX,
                    "num_predict": cls.NUM_PREDICT,
                },
            }

            logger.info(
                "Enviando para Ollama (%s imagem(ns), modelo=%s, num_ctx=%s)",
                len(imagens_base64),
                cls.OLLAMA_MODEL,
                cls.NUM_CTX,
            )

            response = requests.post(
                f"{cls.OLLAMA_HOST}/api/chat", json=payload, timeout=cls.TIMEOUT
            )

            if response.status_code == 404:
                raise RuntimeError(
                    f"Modelo '{cls.OLLAMA_MODEL}' não encontrado. "
                    f"Execute: ollama pull {cls.OLLAMA_MODEL}"
                )
            response.raise_for_status()

            dados_resp = response.json()
            mensagem_resp = dados_resp.get("message", {}) or {}
            texto = (mensagem_resp.get("content") or "").strip()

            if dados_resp.get("done_reason") == "length":
                logger.warning("Resposta cortada por limite de tokens (num_predict).")

            # Remove bloco de raciocínio, se o modelo ainda gerar um.
            texto = re.sub(r"<think>.*?</think>", "", texto, flags=re.DOTALL).strip()

            if "<think>" in texto:
                raise ValueError(
                    "O modelo gastou o limite de tokens raciocinando e não chegou ao JSON. "
                    "Use a variante -instruct ou atualize o Ollama."
                )

            if not texto:
                raise ValueError("O Ollama retornou uma resposta vazia.")

            logger.info("Resposta bruta do modelo: %s", texto[:1500])

            dados = cls._normalizar_resultado(cls._extrair_json(texto), hoje_str)

            if dados["valor_total"] == 0.0 and not dados["itens"]:
                return cls._gerar_resultado_manual(
                    "O modelo não conseguiu identificar valores no documento. "
                    "Por favor, confira os campos e preencha manualmente.",
                    hoje_str,
                )

            dados["ia_sucesso"] = True
            dados["modo_demo"] = False
            return dados

        except requests.exceptions.ConnectionError:
            logger.exception("Não foi possível conectar ao Ollama.")
            return cls._gerar_resultado_manual(
                "O serviço Ollama não está em execução. Os campos estão liberados para preenchimento manual.",
                hoje_str,
            )

        except requests.exceptions.Timeout:
            logger.exception("O processamento excedeu o tempo limite no Ollama.")
            return cls._gerar_resultado_manual(
                "Tempo limite de processamento excedido. Por favor, preencha os dados manualmente.",
                hoje_str,
            )

        except requests.RequestException as e:
            logger.exception("Erro de comunicação com o Ollama: %s", e)
            return cls._gerar_resultado_manual(
                f"Erro de comunicação com o Ollama ({e}). Preencha os dados manualmente.",
                hoje_str,
            )

        except Exception as e:
            logger.exception("Erro ao processar documento com IA: %s", e)
            return cls._gerar_resultado_manual(
                f"Não foi possível extrair dados automaticamente ({e}). Preencha os campos manualmente.",
                hoje_str,
            )

    # ------------------------------------------------------------------
    # Preparação das imagens
    # ------------------------------------------------------------------
    @classmethod
    def _otimizar_imagem(cls, img_bytes: bytes) -> bytes:
        """Corrige rotação, reduz resolução e converte para JPEG."""
        img = Image.open(BytesIO(img_bytes))
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((cls.IMG_LADO_MAX, cls.IMG_LADO_MAX))
        buf = BytesIO()
        img.save(buf, "JPEG", quality=88)
        return buf.getvalue()

    @classmethod
    def _preparar_imagens(cls, file_bytes: bytes, mimetype: str) -> list[bytes]:
        if mimetype.startswith("image/"):
            return [cls._otimizar_imagem(file_bytes)]

        if mimetype == "application/pdf":
            try:
                import fitz  # PyMuPDF
            except ImportError:
                raise RuntimeError("PyMuPDF não está instalado. Execute: pip install pymupdf")

            documento = fitz.open(stream=file_bytes, filetype="pdf")
            try:
                total = len(documento)
                limite = min(total, cls.MAX_PAGINAS_PDF)
                imagens = []
                for n in range(limite):
                    pixmap = documento.load_page(n).get_pixmap(
                        matrix=fitz.Matrix(2, 2), alpha=False
                    )
                    imagens.append(cls._otimizar_imagem(pixmap.tobytes("png")))
            finally:
                documento.close()

            if not imagens:
                raise ValueError("O PDF não possui páginas legíveis.")

            if total > limite:
                logger.warning(
                    "PDF com %s páginas; somente as primeiras %s foram processadas.",
                    total,
                    limite,
                )
            return imagens

        raise ValueError(f"Tipo de arquivo não suportado: {mimetype}")

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------
    @staticmethod
    def _extrair_json(texto: str) -> dict[str, Any]:
        texto = texto.strip()
        texto = re.sub(r"^```json\s*", "", texto, flags=re.IGNORECASE)
        texto = re.sub(r"^```\s*", "", texto)
        texto = re.sub(r"\s*```$", "", texto).strip()

        try:
            resultado = json.loads(texto)
            if isinstance(resultado, dict):
                return resultado
        except json.JSONDecodeError:
            pass

        inicio, fim = texto.find("{"), texto.rfind("}")
        if inicio == -1 or fim <= inicio:
            raise ValueError("O modelo não retornou um JSON válido.")

        try:
            resultado = json.loads(texto[inicio : fim + 1])
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON retornado pelo modelo é inválido: {e}")

        if not isinstance(resultado, dict):
            raise ValueError("A resposta JSON não é um objeto.")
        return resultado

    # ------------------------------------------------------------------
    # Normalização
    # ------------------------------------------------------------------
    @staticmethod
    def _para_float(valor: Any) -> float:
        if isinstance(valor, bool):
            return 0.0
        if isinstance(valor, (int, float)):
            return float(valor)
        if isinstance(valor, str):
            texto = re.sub(r"[^\d,.\-]", "", valor)
            if not texto:
                return 0.0
            if "," in texto and "." in texto:
                if texto.rfind(",") > texto.rfind("."):
                    texto = texto.replace(".", "").replace(",", ".")
                else:
                    texto = texto.replace(",", "")
            elif "," in texto:
                texto = texto.replace(",", ".")
            try:
                return float(texto)
            except ValueError:
                return 0.0
        return 0.0

    @staticmethod
    def _para_texto(valor: Any) -> str:
        return "" if valor is None else str(valor).strip()

    @classmethod
    def _normalizar_data(cls, valor: Any, hoje_str: str) -> str:
        texto = cls._para_texto(valor)

        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", texto):
            try:
                date.fromisoformat(texto)
                return texto
            except ValueError:
                return hoje_str

        m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", texto)
        if m:
            try:
                return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
            except ValueError:
                return hoje_str

        return hoje_str

    @classmethod
    def _detectar_parcelamento_texto(cls, texto: str) -> tuple[bool, int, float]:
        """Detecta menções a parcelamento no texto ou descrição."""
        if not texto:
            return False, 1, 0.0

        # Padrões comuns em comprovantes brasileiros:
        # Ex: "3x de R$ 50,00", "3x de 50.00", "3X 50,00"
        m = re.search(r'(\d{1,2})\s*[xX]\s*(?:de\s*)?R?\$?\s*([\d\.,]+)', texto)
        if m:
            num = int(m.group(1))
            val_str = m.group(2).replace('.', '').replace(',', '.') if ',' in m.group(2) else m.group(2)
            try:
                val = float(val_str)
            except ValueError:
                val = 0.0
            if 1 < num <= 36:
                return True, num, val

        # "parcelas: 3", "parcelado em 3x", "parc 1/6"
        m2 = re.search(r'(?:parcel(?:as?|ado\s*em)|parc\.?)\s*:?\s*(\d{1,2})', texto, re.IGNORECASE)
        if m2:
            num = int(m2.group(1))
            if 1 < num <= 36:
                return True, num, 0.0

        m3 = re.search(r'(\d{1,2})\s*parcelas', texto, re.IGNORECASE)
        if m3:
            num = int(m3.group(1))
            if 1 < num <= 36:
                return True, num, 0.0

        return False, 1, 0.0

    @classmethod
    def _normalizar_resultado(cls, dados: dict[str, Any], hoje_str: str) -> dict[str, Any]:
        tipo_transacao = cls._para_texto(dados.get("tipo_transacao")).lower()
        if tipo_transacao not in cls.TIPOS_TRANSACAO:
            tipo_transacao = "despesa"

        forma_pagamento = cls._para_texto(dados.get("forma_pagamento"))
        if forma_pagamento not in cls.FORMAS_PAGAMENTO:
            forma_pagamento = ""

        tipo_receita = cls._para_texto(dados.get("tipo_receita"))
        if tipo_receita not in cls.TIPOS_RECEITA:
            tipo_receita = "Outros"

        itens_brutos = dados.get("itens")
        if not isinstance(itens_brutos, list):
            itens_brutos = []

        itens = []
        for item in itens_brutos:
            if not isinstance(item, dict):
                continue
            itens.append(
                {
                    "descricao": cls._para_texto(item.get("descricao")),
                    "valor": round(cls._para_float(item.get("valor")), 2),
                    "categoria_sugerida": cls._para_texto(item.get("categoria_sugerida")),
                }
            )

        valor_total = round(cls._para_float(dados.get("valor_total")), 2)
        observacoes = cls._para_texto(dados.get("observacoes"))

        # Extração de dados de parcelamento
        is_parcelado = bool(dados.get("is_parcelado", False))
        numero_parcelas = int(cls._para_float(dados.get("numero_parcelas") or 1))
        valor_parcela = round(cls._para_float(dados.get("valor_parcela")), 2)

        # Se a IA não sinalizou no schema mas o texto tem evidência de parcelamento
        if not is_parcelado or numero_parcelas <= 1:
            texto_busca = f"{observacoes} {dados.get('estabelecimento', '')} " + " ".join(i.get('descricao', '') for i in itens)
            det, num_det, val_det = cls._detectar_parcelamento_texto(texto_busca)
            if det:
                is_parcelado = True
                numero_parcelas = num_det
                if val_det > 0 and valor_parcela == 0:
                    valor_parcela = val_det

        if numero_parcelas > 1:
            is_parcelado = True
            if not forma_pagamento or forma_pagamento not in ["Cartão de Crédito"]:
                forma_pagamento = "Cartão de Crédito"
            if valor_parcela == 0 and valor_total > 0:
                valor_parcela = round(valor_total / numero_parcelas, 2)
        else:
            numero_parcelas = 1

        if itens:
            soma = round(sum(i["valor"] for i in itens), 2)
            if soma == 0.0 and valor_total > 0:
                # Itens sem valor no documento: lança um único item com o total.
                primeiro = itens[0]
                itens = [
                    {
                        "descricao": primeiro["descricao"] or "Total do documento",
                        "valor": valor_total,
                        "categoria_sugerida": primeiro["categoria_sugerida"],
                    }
                ]
                aviso = (
                    "Os itens do documento estavam com valor 0,00; foi lançado "
                    "um único item com o valor total."
                )
                observacoes = f"{observacoes}\n{aviso}".strip()
            elif valor_total == 0.0 and soma > 0:
                valor_total = soma
            elif abs(soma - valor_total) > 0.05:
                aviso = (
                    f"ATENÇÃO: a soma dos itens (R$ {soma:.2f}) difere do "
                    f"total (R$ {valor_total:.2f}). Confira antes de salvar."
                )
                observacoes = f"{observacoes}\n{aviso}".strip()

        return {
            "tipo_transacao": tipo_transacao,
            "data": cls._normalizar_data(dados.get("data"), hoje_str),
            "estabelecimento": cls._para_texto(dados.get("estabelecimento")),
            "forma_pagamento": forma_pagamento,
            "tipo_receita": tipo_receita,
            "valor_total": valor_total,
            "is_parcelado": is_parcelado,
            "numero_parcelas": numero_parcelas,
            "valor_parcela": valor_parcela,
            "itens": itens,
            "observacoes": observacoes,
        }

    # ------------------------------------------------------------------
    # Erro
    # ------------------------------------------------------------------

    @classmethod
    def _gerar_resultado_manual(cls, mensagem: str, hoje_str: str) -> dict[str, Any]:
        return cls._gerar_resultado_erro(mensagem, hoje_str)

    @staticmethod
    def _gerar_resultado_erro(mensagem: str, hoje_str: str) -> dict[str, Any]:
        """Estrutura segura em caso de falha. Nunca gera valores fictícios."""
        return {
            "tipo_transacao": "",
            "data": hoje_str,
            "estabelecimento": "",
            "forma_pagamento": "",
            "tipo_receita": "",
            "valor_total": 0.00,
            "is_parcelado": False,
            "numero_parcelas": 1,
            "valor_parcela": 0.0,
            "itens": [],
            "observacoes": "",
            "ia_sucesso": False,
            "modo_demo": False,
            "erro_msg": mensagem,
        }