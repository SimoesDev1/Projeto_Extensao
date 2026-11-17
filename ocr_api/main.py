import hmac
import logging
import os
import sys
import threading
from pathlib import Path

# Garante que a raiz do projeto esteja no sys.path mesmo se iniciado dentro de ocr_api
_RAIZ_PROJETO = str(Path(__file__).resolve().parents[1])
if _RAIZ_PROJETO not in sys.path:
    sys.path.insert(0, _RAIZ_PROJETO)

from fastapi import FastAPI, File, Header, HTTPException, UploadFile

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("doce-ocr-api")

app = FastAPI(
    title="Doce como Mel - OCR API",
    version="1.2.0",
    description="Serviço independente para processamento OCR e extração fiscal de documentos",
)

OCR_LOCK = threading.Lock()

MAX_FILE_MB = int(os.getenv("OCR_MAX_FILE_MB", "16"))
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "doce-como-mel-ocr",
        "ocr": "lazy",
    }


def validar_token(token: str | None):
    esperado = os.getenv("OCR_API_TOKEN", "doce-ocr-secret-key").strip()

    if not esperado:
        # Se não houver token configurado explicitamente, permite acesso em desenvolvimento
        return

    if not token or not hmac.compare_digest(token.strip(), esperado):
        raise HTTPException(
            status_code=401,
            detail="Token de autenticação inválido.",
        )


@app.post("/processar")
def processar_documento(
    arquivo: UploadFile = File(...),
    x_api_key: str | None = Header(default=None),
):
    validar_token(x_api_key)

    nome = (arquivo.filename or "").lower()
    tipo = (arquivo.content_type or "").lower()

    if nome.endswith(".pdf") or tipo == "application/pdf":
        modo = "pdf"
    elif (
        nome.endswith((".png", ".jpg", ".jpeg", ".webp"))
        or tipo.startswith("image/")
    ):
        modo = "imagem"
    else:
        raise HTTPException(
            status_code=415,
            detail="Formato não suportado. Envie PDF ou imagem (PNG, JPG, WEBP).",
        )

    conteudo = arquivo.file.read(MAX_FILE_BYTES + 1)

    if not conteudo:
        raise HTTPException(
            status_code=400,
            detail="Arquivo vazio.",
        )

    if len(conteudo) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Arquivo excede o limite de {MAX_FILE_MB} MB.",
        )

    if not OCR_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail="Outro documento está sendo processado. Aguarde e tente novamente.",
        )

    try:
        logger.info(
            "DOCUMENTO | início | modo=%s | tamanho=%.2f MB",
            modo,
            len(conteudo) / (1024 * 1024),
        )

        from app.services.ocr_worker_service import (
            OCROcupadoError,
            OCRTimeoutError,
            executar_nota_fiscal_bytes,
            executar_pdf_bytes,
        )

        try:
            if modo == "pdf":
                resultado, tempo = executar_pdf_bytes(conteudo)
            else:
                resultado, tempo = executar_nota_fiscal_bytes(conteudo)

        except OCROcupadoError as exc:
            raise HTTPException(
                status_code=429,
                detail=str(exc),
            ) from exc

        except OCRTimeoutError as exc:
            raise HTTPException(
                status_code=504,
                detail=str(exc),
            ) from exc

        if not isinstance(resultado, dict):
            raise RuntimeError("O worker retornou dados fiscais inválidos.")

        logger.info(
            "DOCUMENTO | concluído | modo=%s | tempo=%.2fs",
            modo,
            tempo,
        )

        return {
            "ok": True,
            "modo": modo,
            "resultado": resultado,
            "tempo": round(tempo, 2),
        }

    except HTTPException:
        raise

    except Exception:
        logger.exception("Falha no processamento OCR")

        raise HTTPException(
            status_code=500,
            detail="Falha ao processar o documento.",
        )

    finally:
        OCR_LOCK.release()
