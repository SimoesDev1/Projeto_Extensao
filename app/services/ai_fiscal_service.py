import base64
import hashlib
import json
import logging
import os
import re
from datetime import date
from io import BytesIO
from typing import Any

import requests
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

# Cache de resultados por hash SHA-256 do arquivo para evitar chamadas redundantes e economizar tokens
_CACHE_IA_FISCAL: dict[str, dict[str, Any]] = {}


class AiFiscalService:
    """
    Serviço fiscal inteligente para leitura de comprovantes e notas fiscais.
    Suporta:
      - 'gemini': Google Gemini Vision (otimizado com economia de tokens e cache)
      - 'ollama': modelo local (Qwen3-VL)
      - 'texto': extração direta via OCR / extrator local sem IA de nuvem
    """

    OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3-vl:4b-instruct")
    TIMEOUT = min(int(os.getenv("OLLAMA_TIMEOUT", "90")), 95)
    NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
    NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "2048"))
    KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")
    IMG_LADO_MAX = int(os.getenv("OCR_MAX_LADO", "1280"))
    MAX_PAGINAS_PDF = 5

    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
    GEMINI_TIMEOUT = int(os.getenv("GEMINI_TIMEOUT", "30"))

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
    # Disponibilidade e Provedores
    # ------------------------------------------------------------------
    @classmethod
    def get_provider(cls) -> str:
        prov = os.getenv("AI_PROVIDER", "").strip().lower()
        if prov in ("gemini", "ollama", "texto"):
            return prov
        if cls.is_gemini_configurado():
            return "gemini"
        return "texto"

    @classmethod
    def is_gemini_configurado(cls) -> bool:
        chave = os.getenv("GEMINI_API_KEY", "").strip()
        return bool(chave and not chave.startswith("AQ.placeholder"))

    @classmethod
    def is_configurado(cls) -> bool:
        provider = cls.get_provider()
        if provider == "gemini":
            return cls.is_gemini_configurado()
        if provider == "texto":
            return True
        if provider == "ollama":
            import time as _t
            agora = _t.time()
            cache = getattr(cls, "_cfg_cache", None)
            if cache and agora - cache[0] < 120:
                return cache[1]
            ok = cls._verificar_ollama_configurado()
            cls._cfg_cache = (agora, ok)
            return ok
        return True

    @classmethod
    def _verificar_ollama_configurado(cls) -> bool:
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
        except Exception as e:
            logger.debug("Ollama não está disponível: %s", e)
            return False

    # ------------------------------------------------------------------
    # Ponto de Entrada da Interpretação
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

        # 1. Verifica cache por hash (evita chamadas redundantes e custo de tokens)
        file_hash = hashlib.sha256(file_bytes).hexdigest()
        if file_hash in _CACHE_IA_FISCAL:
            logger.info("CACHE IA | Documento recuperado do cache em memória (hash=%s)", file_hash[:12])
            cached = dict(_CACHE_IA_FISCAL[file_hash])
            cached["data"] = cls._normalizar_data(cached.get("data"), hoje_str)
            return cached

        resultado = None

        if provider == "gemini":
            if cls.is_gemini_configurado():
                resultado = cls._processar_com_gemini(file_bytes, mimetype, hoje_str, categorias_existentes)
            else:
                logger.warning("GEMINI_API_KEY não configurada. Usando extrator local.")
                resultado = cls._processar_com_extrator_texto(file_bytes, mimetype, hoje_str, categorias_existentes)

        elif provider == "ollama":
            if cls.is_configurado():
                resultado = cls._processar_com_ollama(file_bytes, mimetype, hoje_str, categorias_existentes)
            else:
                logger.warning("Ollama indisponível. Usando extrator local como alternativa.")
                resultado = cls._processar_com_extrator_texto(file_bytes, mimetype, hoje_str, categorias_existentes)

        else:
            resultado = cls._processar_com_extrator_texto(file_bytes, mimetype, hoje_str, categorias_existentes)

        # Salva em cache se obteve sucesso
        if resultado and resultado.get("ia_sucesso"):
            _CACHE_IA_FISCAL[file_hash] = resultado

        return resultado

    # ------------------------------------------------------------------
    # Integração Gemini (Econômica, Rápida e com Precisão Fiscal)
    # ------------------------------------------------------------------
    @classmethod
    def _processar_com_gemini(
        cls,
        file_bytes: bytes,
        mimetype: str,
        hoje_str: str,
        categorias_existentes: list[str],
    ) -> dict[str, Any]:
        """
        Processa com Gemini Vision minimizando tokens:
        1. Pré-executa extração textual local (gratuita e rápida).
        2. Se a extração local foi 100% conclusiva (fornecedor + data + valor > 0 + itens), usa direto!
        3. Caso contrário, envia ao Gemini o texto local já extraído + imagem otimizada.
        """
        import time as _t
        _t0 = _t.perf_counter()

        # Otimização 1: Tenta extração local primeiro (0 tokens de nuvem)
        dados_local = None
        try:
            dados_local = cls._obter_dados_extrator_bruto(file_bytes, mimetype)
        except Exception as e:
            logger.debug("Extração preliminar não disponível: %s", e)

        # Se o extrator local identificou fornecedor, data e valor com itens válidos, poupa 100% dos tokens
        if dados_local and isinstance(dados_local, dict):
            val_total = cls._para_float(dados_local.get("valor_total"))
            fornec = str(dados_local.get("fornecedor") or "").strip()
            data_doc = str(dados_local.get("data") or "").strip()
            itens_doc = dados_local.get("itens") or []

            if val_total > 0 and fornec and data_doc and len(itens_doc) > 0:
                logger.info("GEMINI OTIMIZAÇÃO | Documento perfeitamente estruturado localmente. 0 tokens consumidos.")
                convertido = cls._converter_extrator_para_revisao(dados_local, hoje_str, categorias_existentes)
                convertido["ia_sucesso"] = True
                convertido["modo_demo"] = False
                convertido["observacoes"] = (convertido.get("observacoes") or "") + " [Processamento Otimizado Local]"
                return convertido

        # Se precisa de visão computacional da IA para enriquecer/encontrar dados
        try:
            import google.generativeai as genai

            api_key = os.getenv("GEMINI_API_KEY", "").strip()
            genai.configure(api_key=api_key)

            categorias_str = (
                ", ".join(categorias_existentes)
                if categorias_existentes
                else "Ingredientes, Embalagens, Operacional, Marketing, Outros"
            )

            prompt = f"""Você é o extrator fiscal inteligente do sistema 'Doce como Mel' (confeitaria).
Analise o documento fiscal anexo e responda estritamente com um objeto JSON válido.

REGRAS ESTRITAS DE PRECISÃO FISCAL:
1. NÃO invente ou alucine dados ausentes. Não gere itens fictícios nem deduza formas de pagamento sem evidência escrita explícita.
2. tipo_transacao: 'despesa' (compra de insumos/ingredientes/embalagens/serviços) ou 'receita' (venda a cliente).
3. data: formato AAAA-MM-DD exatamente como no documento. Se não visível, informe '{hoje_str}'.
4. estabelecimento: nome ou razão social do emissor/fornecedor (ou cliente em caso de receita).
5. forma_pagamento: uma entre {", ".join(cls.FORMAS_PAGAMENTO)}.
   ATENÇÃO: Informe somente se houver texto explícito no documento (ex: 'PIX', 'DINHEIRO', 'CARTÃO DE CRÉDITO', 'CARTÃO DE DÉBITO'). Se não houver texto explícito, retorne string vazia "".
6. tipo_receita: uma entre {", ".join(cls.TIPOS_RECEITA)}. Padrão: 'Outros'.
7. valor_total: número decimal positivo correspondente ao total da nota/recibo (ex: 154.80).
8. is_parcelado: true somente se houver menção explícita de parcelamento (ex: '3x', 'parc. 1/4').
9. numero_parcelas: quantidade de parcelas se parcelado, ou 1 se à vista.
10. valor_parcela: valor da parcela se especificado, ou 0.
11. itens: liste SOMENTE os produtos ou serviços individualizados que estão discriminados no documento, com 'descricao', 'valor' e 'categoria_sugerida' (escolha entre: {categorias_str}). Se o comprovante tiver apenas o total geral sem lista de itens, retorne lista vazia []. NUNCA crie itens genéricos com o valor total.
12. observacoes: breve resumo objetivo da compra/operação (máximo 1 frase).

Estrutura JSON obrigatória:
{{
  "tipo_transacao": "despesa",
  "data": "AAAA-MM-DD",
  "estabelecimento": "...",
  "forma_pagamento": "...",
  "tipo_receita": "Outros",
  "valor_total": 0.00,
  "is_parcelado": false,
  "numero_parcelas": 1,
  "valor_parcela": 0.0,
  "itens": [
    {{"descricao": "...", "valor": 0.00, "categoria_sugerida": "..."}}
  ],
  "observacoes": "..."
}}
"""
            # Se já temos texto parcial do OCR local, inclui como contexto para reduzir esforço da IA
            if dados_local and isinstance(dados_local, dict):
                texto_previo = json.dumps(dados_local, ensure_ascii=False)
                prompt += f"\n\nContexto preliminar detectado no documento:\n{texto_previo[:1000]}"

            modelo = genai.GenerativeModel(
                model_name=cls.GEMINI_MODEL,
                generation_config={
                    "temperature": 0.0,
                    "response_mime_type": "application/json",
                }
            )

            # Prepara o payload de arquivo otimizado
            partes = []
            if mimetype == "application/pdf":
                partes.append({"mime_type": "application/pdf", "data": file_bytes})
            else:
                # Otimiza imagem antes do envio para economizar tokens de visão
                img_otimizada = cls._otimizar_imagem(file_bytes)
                partes.append({"mime_type": "image/jpeg", "data": img_otimizada})

            partes.append(prompt)

            logger.info(
                "GEMINI API | Chamando %s | MIME: %s | Bytes: %d",
                cls.GEMINI_MODEL,
                mimetype,
                len(file_bytes),
            )

            resposta = modelo.generate_content(partes)

            texto_resposta = (resposta.text or "").strip()
            dados_brutos = cls._extrair_json(texto_resposta)
            dados_normalizados = cls._normalizar_resultado(dados_brutos, hoje_str)

            # Se a IA não pegou estabelecimento mas o OCR local tinha, aproveita
            if not dados_normalizados.get("estabelecimento") and dados_local:
                dados_normalizados["estabelecimento"] = dados_local.get("fornecedor", "")

            # Se a IA não pegou itens mas o OCR local pegou, aproveita
            if not dados_normalizados.get("itens") and dados_local and dados_local.get("itens"):
                for it in dados_local.get("itens"):
                    dados_normalizados["itens"].append({
                        "descricao": str(it.get("descricao") or "Item"),
                        "valor": cls._para_float(it.get("valor")),
                        "categoria_sugerida": str(it.get("categoria_sugerida") or "Outros"),
                    })

            dados_normalizados["ia_sucesso"] = True
            dados_normalizados["modo_demo"] = False
            logger.info("GEMINI API | Sucesso em %.2fs | Total: %.2f", _t.perf_counter() - _t0, dados_normalizados.get("valor_total", 0))
            return dados_normalizados

        except Exception as exc:
            logger.warning("Falha ao processar com Gemini (%s). Acionando extrator local de contingência.", exc)
            return cls._processar_com_extrator_texto(file_bytes, mimetype, hoje_str, categorias_existentes)

    # ------------------------------------------------------------------
    # Processamento com Extrator Texto / OCR (Monólito ou FastAPI)
    # ------------------------------------------------------------------
    @classmethod
    def _obter_dados_extrator_bruto(cls, file_bytes: bytes, mimetype: str) -> dict[str, Any]:
        """Obtém os dados brutos via API FastAPI (se configurada) ou subprocesso local."""
        from app.services.ocr_worker_service import (
            OCROcupadoError,
            OCRTimeoutError,
            obter_timeout_processamento,
            executar_nota_fiscal_bytes,
            executar_pdf_bytes,
        )

        TIMEOUT_S = obter_timeout_processamento()
        modo = "pdf" if mimetype.endswith("pdf") else "imagem"
        nome_arquivo = "documento.pdf" if modo == "pdf" else "documento.png"

        api_url = os.getenv("OCR_API_URL", "").strip().rstrip("/")
        api_token = os.getenv("OCR_API_TOKEN", "doce-ocr-secret-key").strip()

        # 1. Se API FastAPI estiver configurada, tenta chamada HTTP primeiro
        if api_url:
            try:
                url = f"{api_url}/processar"
                logger.info("EXTRATOR API | Chamando FastAPI OCR: %s", url)
                resp = requests.post(
                    url,
                    headers={"X-API-Key": api_token},
                    files={"arquivo": (nome_arquivo, file_bytes, mimetype)},
                    timeout=(10, TIMEOUT_S + 20),
                )
                if resp.status_code == 429:
                    raise OCROcupadoError("A API OCR está processando outro documento.")
                if resp.status_code in (408, 504):
                    raise OCRTimeoutError("A API OCR excedeu o tempo de processamento.")

                resp.raise_for_status()
                dados_api = resp.json()
                if isinstance(dados_api, dict) and dados_api.get("ok"):
                    resultado = dados_api.get("resultado")
                    if isinstance(resultado, dict):
                        return resultado

            except OCROcupadoError:
                raise
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                logger.warning("FastAPI OCR não respondeu (%s). Usando worker local.", exc)
            except Exception as exc:
                logger.warning("Erro ao consultar FastAPI OCR: %s. Usando worker local.", exc)

        # 2. Fallback resiliente: executa diretamente via worker independente em subprocesso
        logger.info("EXTRATOR WORKER | Executando worker local independente (modo=%s)", modo)
        if modo == "pdf":
            resultado, _ = executar_pdf_bytes(file_bytes)
        else:
            resultado, _ = executar_nota_fiscal_bytes(file_bytes)

        if not isinstance(resultado, dict):
            raise ValueError("O worker retornou formato de dados inválido.")

        return resultado

    @classmethod
    def _processar_com_extrator_texto(
        cls,
        file_bytes: bytes,
        mimetype: str,
        hoje_str: str,
        categorias_existentes: list[str],
    ) -> dict[str, Any]:
        """Processa documentos via extrator de texto/OCR sem IA de nuvem."""
        import time as _time
        from app.services.ocr_worker_service import OCROcupadoError, OCRTimeoutError

        _t0 = _time.perf_counter()

        try:
            dados_extrator = cls._obter_dados_extrator_bruto(file_bytes, mimetype)

            dados_convertidos = cls._converter_extrator_para_revisao(
                dados_extrator,
                hoje_str,
                categorias_existentes,
            )

            logger.info(
                "EXTRATOR | Conversão concluída em %.2fs | Fornecedor: %r | Total: %.2f",
                _time.perf_counter() - _t0,
                dados_convertidos.get("estabelecimento"),
                dados_convertidos.get("valor_total", 0),
            )

            if dados_convertidos["valor_total"] == 0.0 and not dados_convertidos["itens"]:
                return cls._gerar_resultado_manual(
                    "Não foi possível identificar valores automaticamente no documento. "
                    "Por favor, confira os campos e preencha manualmente.",
                    hoje_str,
                )

            dados_convertidos["ia_sucesso"] = True
            dados_convertidos["modo_demo"] = False
            return dados_convertidos

        except OCROcupadoError:
            raise

        except OCRTimeoutError as exc:
            logger.error("EXTRATOR | Timeout ao processar documento: %s", exc)
            return cls._gerar_resultado_manual(
                "Tempo limite de processamento excedido. Por favor, preencha os dados manualmente.",
                hoje_str,
            )

        except Exception as e:
            logger.exception("EXTRATOR | Erro na extração: %s", e)
            return cls._gerar_resultado_manual(
                "Erro na extração automática. Por favor, preencha os dados manualmente.",
                hoje_str,
            )

    # ------------------------------------------------------------------
    # Conversão do Extrator Local para a Tela de Revisão
    # ------------------------------------------------------------------
    @classmethod
    def _converter_extrator_para_revisao(
        cls,
        d: dict[str, Any],
        hoje_str: str,
        categorias_existentes: list[str],
    ) -> dict[str, Any]:
        """
        Converte o dicionário {fornecedor, data, forma_pagamento, parcelas, valor_total, itens}
        para a estrutura usada pela tela de revisão e banco de dados.
        Preserva rigorosamente a precisão fiscal, sem inventar valores ou itens falsos.
        """
        fornecedor = str(d.get("fornecedor") or "").strip()
        data_str = str(d.get("data") or "").strip()
        if not data_str:
            data_str = hoje_str

        fp = str(d.get("forma_pagamento") or "").strip()
        if fp not in cls.FORMAS_PAGAMENTO:
            import unicodedata
            fpu = "".join(
                c for c in unicodedata.normalize("NFD", fp.upper())
                if unicodedata.category(c) != "Mn"
            )

            if re.search(r"\bCARTAO\s+(?:DE\s+)?CREDITO\b", fpu):
                fp = "Cartão de Crédito"
            elif re.search(r"\bCARTAO\s+(?:DE\s+)?DEBITO\b", fpu):
                fp = "Cartão de Débito"
            elif re.search(r"\bPIX\b|CHAVE\s+PIX", fpu):
                fp = "Pix"
            elif re.search(r"\bDINHEIRO\b|\bESPECIE\b", fpu):
                fp = "Dinheiro"
            elif re.search(r"\bTRANSFERENCIA\b|\bTED\b|\bTEF\b", fpu):
                fp = "Transferência"
            else:
                fp = ""  # Usuário escolhe manualmente na revisão: nunca inventa!

        parcelas = int(d.get("parcelas") or 1)
        is_parcelado = parcelas > 1
        valor_total = round(cls._para_float(d.get("valor_total")), 2)
        valor_parcela = round(valor_total / parcelas, 2) if is_parcelado and parcelas > 0 else 0.0

        itens_in = d.get("itens") or []
        itens_out = []
        cat_padrao = categorias_existentes[0] if categorias_existentes else "Outros"

        for item in itens_in:
            if isinstance(item, dict):
                desc = str(item.get("descricao") or "").strip()
                val = round(cls._para_float(item.get("valor")), 2)
                cat = str(item.get("categoria_sugerida") or "").strip()
                if not cat:
                    cat = cat_padrao
                if desc and val > 0:
                    itens_out.append({
                        "descricao": desc,
                        "valor": val,
                        "categoria_sugerida": cat,
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
            "observacoes": f"Extraído via Extrator Fiscal. Fornecedor: {fornecedor}" if fornecedor else "Extraído via Extrator Fiscal.",
        }

    # ------------------------------------------------------------------
    # Processamento Ollama
    # ------------------------------------------------------------------
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

Transcreva exatamente o que estiver visível, sem inventar dados.

Campos:
- tipo_transacao: "despesa" ou "receita".
- data: AAAA-MM-DD. Se não estiver visível, use "{hoje_str}".
- estabelecimento: nome do emissor, fornecedor ou cliente.
- forma_pagamento: uma entre {", ".join(cls.FORMAS_PAGAMENTO)}. Informe somente com evidência explícita. Se não houver, retorne "".
- tipo_receita: uma entre {", ".join(cls.TIPOS_RECEITA)}. Padrão: "Outros".
- valor_total: total pago/cobrado (ex.: 85.50).
- is_parcelado: true se parcelado, false caso contrário.
- numero_parcelas: número de parcelas ou 1.
- valor_parcela: valor de cada parcela ou 0.
- itens: liste somente produtos ou serviços individualizados [descricao, valor, categoria_sugerida]. Se não houver itens discriminados, retorne [].
- observacoes: resumo curto.

Categorias existentes: {categorias_str}
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

            response = requests.post(
                f"{cls.OLLAMA_HOST}/api/chat", json=payload, timeout=cls.TIMEOUT
            )
            response.raise_for_status()

            dados_resp = response.json()
            mensagem_resp = dados_resp.get("message", {}) or {}
            texto = (mensagem_resp.get("content") or "").strip()
            texto = re.sub(r"<think>.*?</think>", "", texto, flags=re.DOTALL).strip()

            if not texto:
                raise ValueError("O Ollama retornou uma resposta vazia.")

            dados = cls._normalizar_resultado(cls._extrair_json(texto), hoje_str)

            if dados["valor_total"] == 0.0 and not dados["itens"]:
                return cls._gerar_resultado_manual(
                    "O modelo não conseguiu identificar valores no documento.",
                    hoje_str,
                )

            dados["ia_sucesso"] = True
            dados["modo_demo"] = False
            return dados

        except Exception as e:
            logger.warning("Falha no Ollama: %s. Utilizando extrator local.", e)
            return cls._processar_com_extrator_texto(file_bytes, mimetype, hoje_str, categorias_existentes)

    # ------------------------------------------------------------------
    # Otimização e Manipulação de Imagens
    # ------------------------------------------------------------------
    @classmethod
    def _otimizar_imagem(cls, img_bytes: bytes) -> bytes:
        """Corrige rotação, reduz dimensões e converte para JPEG otimizado."""
        img = Image.open(BytesIO(img_bytes))
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((cls.IMG_LADO_MAX, cls.IMG_LADO_MAX), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, "JPEG", quality=85, optimize=True)
        return buf.getvalue()

    @classmethod
    def _preparar_imagens(cls, file_bytes: bytes, mimetype: str) -> list[bytes]:
        if mimetype.startswith("image/"):
            return [cls._otimizar_imagem(file_bytes)]

        if mimetype == "application/pdf":
            try:
                import fitz
                documento = fitz.open(stream=file_bytes, filetype="pdf")
                try:
                    total = len(documento)
                    limite = min(total, cls.MAX_PAGINAS_PDF)
                    imagens = []
                    for n in range(limite):
                        pixmap = documento.load_page(n).get_pixmap(
                            matrix=fitz.Matrix(1.5, 1.5), alpha=False
                        )
                        imagens.append(cls._otimizar_imagem(pixmap.tobytes("png")))
                    return imagens
                finally:
                    documento.close()
            except ImportError:
                # Se PyMuPDF não estiver instalado, utiliza pdfplumber/fallback
                import pdfplumber
                with pdfplumber.open(BytesIO(file_bytes)) as pdf:
                    imagens = []
                    for p in pdf.pages[:cls.MAX_PAGINAS_PDF]:
                        pil_img = p.to_image(resolution=150).original
                        buf = BytesIO()
                        pil_img.save(buf, "PNG")
                        imagens.append(cls._otimizar_imagem(buf.getvalue()))
                    return imagens

        raise ValueError(f"Tipo de arquivo não suportado: {mimetype}")

    # ------------------------------------------------------------------
    # Parsing e Normalização
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
        if inicio != -1 and fim > inicio:
            try:
                resultado = json.loads(texto[inicio : fim + 1])
                if isinstance(resultado, dict):
                    return resultado
            except json.JSONDecodeError:
                pass

        raise ValueError("Não foi possível extrair um objeto JSON válido da resposta.")

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

        m = re.fullmatch(r"(\d{2})[/.-](\d{2})[/.-](\d{2}|\d{4})", texto)
        if m:
            dia = int(m.group(1))
            mes = int(m.group(2))
            ano = int(m.group(3))
            if ano < 100:
                ano += 2000 if ano <= 69 else 1900
            try:
                return date(ano, mes, dia).isoformat()
            except ValueError:
                return hoje_str

        return hoje_str

    @classmethod
    def _detectar_parcelamento_texto(cls, texto: str) -> tuple[bool, int, float]:
        if not texto:
            return False, 1, 0.0

        m = re.search(r"(?:parc(?:ela|elas)?\.?\s*)?\d{1,2}\s*/\s*(\d{1,2})", texto, re.IGNORECASE)
        if m:
            num = int(m.group(1))
            if 2 <= num <= 36:
                return True, num, 0.0

        m = re.search(r"(\d{1,2})\s*[xX]\s*(?:de\s*)?R?\$?\s*([\d.,]+)", texto)
        if m:
            num = int(m.group(1))
            if 2 <= num <= 36:
                val_str = m.group(2).replace(".", "").replace(",", ".") if "," in m.group(2) else m.group(2)
                try:
                    valor = float(val_str)
                except ValueError:
                    valor = 0.0
                return True, num, valor

        m = re.search(r"(?:parcel(?:as?|ado\s*em)|parc\.?)\s*:?\s*(\d{1,2})(?!\s*/)", texto, re.IGNORECASE)
        if m:
            num = int(m.group(1))
            if 2 <= num <= 36:
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
            desc = cls._para_texto(item.get("descricao"))
            val = round(cls._para_float(item.get("valor")), 2)
            cat = cls._para_texto(item.get("categoria_sugerida"))
            if desc and val > 0:
                itens.append({
                    "descricao": desc,
                    "valor": val,
                    "categoria_sugerida": cat,
                })

        valor_total = round(cls._para_float(dados.get("valor_total")), 2)
        observacoes = cls._para_texto(dados.get("observacoes"))

        # Parcelamento
        valor_booleano = dados.get("is_parcelado", False)
        if isinstance(valor_booleano, bool):
            is_parcelado = valor_booleano
        elif isinstance(valor_booleano, str):
            is_parcelado = valor_booleano.strip().lower() in ("true", "1", "sim", "yes")
        else:
            is_parcelado = valor_booleano == 1

        numero_parcelas = int(cls._para_float(dados.get("numero_parcelas") or 1))
        numero_parcelas = max(1, min(numero_parcelas, 36))
        valor_parcela = round(cls._para_float(dados.get("valor_parcela")), 2)

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
            if valor_parcela == 0 and valor_total > 0:
                valor_parcela = round(valor_total / numero_parcelas, 2)
        else:
            numero_parcelas = 1

        if itens:
            soma = round(sum(item["valor"] for item in itens), 2)
            if valor_total == 0.0 and soma > 0:
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
    # Tratamento Seguro de Erro (Nunca Inventa Dados Fictícios)
    # ------------------------------------------------------------------
    @classmethod
    def _gerar_resultado_manual(cls, mensagem: str, hoje_str: str) -> dict[str, Any]:
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
