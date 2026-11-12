import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Um único processamento pesado por vez neste processo Flask.
OCR_LOCK = threading.Lock()


class OCROcupadoError(RuntimeError):
    pass


class OCRTimeoutError(TimeoutError):
    pass


def obter_timeout_processamento():
    try:
        valor = int(
            os.getenv(
                "OCR_PROCESS_TIMEOUT",
                os.getenv("EXTRACAO_TIMEOUT", "75"),
            )
        )
    except (TypeError, ValueError):
        valor = 75

    return max(15, min(valor, 100))


def _executar_processo(conteudo: bytes, modo: str):
    if not isinstance(conteudo, bytes) or not conteudo:
        raise ValueError("O arquivo enviado está vazio ou é inválido.")

    if modo not in ("planilha", "nota", "pdf"):
        raise ValueError("Tipo de processamento inválido.")

    if not OCR_LOCK.acquire(blocking=False):
        raise OCROcupadoError(
            "Já existe outro documento sendo processado. "
            "Aguarde a conclusão e tente novamente."
        )

    inicio = time.perf_counter()

    try:
        raiz = Path(__file__).resolve().parents[2]
        worker = raiz / "ocr_worker_process.py"

        if not worker.is_file():
            raise RuntimeError(
                "O processo independente de documentos não foi encontrado."
            )

        timeout = obter_timeout_processamento()

        with tempfile.TemporaryDirectory(
            prefix="doce_documento_"
        ) as pasta:
            arquivo_entrada = Path(pasta) / "entrada.bin"
            arquivo_saida = Path(pasta) / "resposta.json"

            arquivo_entrada.write_bytes(conteudo)

            comando = [
                sys.executable,
                str(worker),
                "--image",
                str(arquivo_entrada),
                "--output",
                str(arquivo_saida),
                "--mode",
                modo,
            ]

            ambiente = os.environ.copy()
            ambiente["PYTHONUNBUFFERED"] = "1"

            logger.info(
                "DOCUMENTO | Iniciando subprocesso | modo=%s | "
                "tamanho=%.2f MB | timeout=%ss",
                modo,
                len(conteudo) / (1024 * 1024),
                timeout,
            )

            try:
                processo = subprocess.run(
                    comando,
                    cwd=str(raiz),
                    env=ambiente,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                logger.error(
                    "DOCUMENTO | TIMEOUT | modo=%s | limite=%ss",
                    modo,
                    timeout,
                )
                raise OCRTimeoutError(
                    f"O processamento excedeu o limite de {timeout} segundos."
                ) from exc

            if processo.stderr:
                for linha in processo.stderr.splitlines():
                    logger.info(
                        "DOCUMENTO WORKER | %s",
                        linha[-1000:],
                    )

            if processo.returncode != 0:
                detalhe = (
                    processo.stderr or processo.stdout or ""
                )[-3000:]

                raise RuntimeError(
                    "O processo independente terminou com erro "
                    f"(código {processo.returncode}): {detalhe}"
                )

            if not arquivo_saida.is_file():
                raise RuntimeError(
                    "O processo terminou sem gerar a resposta."
                )

            try:
                resposta = json.loads(
                    arquivo_saida.read_text(encoding="utf-8")
                )
            except (json.JSONDecodeError, OSError) as exc:
                raise RuntimeError(
                    "O processo retornou uma resposta inválida."
                ) from exc

            if not isinstance(resposta, dict) or not resposta.get("ok"):
                mensagem = (
                    resposta.get("erro", "Falha no processamento.")
                    if isinstance(resposta, dict)
                    else "Resposta inválida do processo independente."
                )
                raise RuntimeError(mensagem)

            resultado = resposta.get("resultado")

            if modo == "pdf" and not isinstance(resultado, dict):
                raise RuntimeError(
                    "O worker PDF retornou dados em formato inválido."
                )

            if modo != "pdf" and not isinstance(resultado, list):
                raise RuntimeError(
                    "O worker OCR retornou dados em formato inválido."
                )

            tempo_total = time.perf_counter() - inicio

            logger.info(
                "DOCUMENTO | Processo concluído | modo=%s | tempo=%.2fs",
                modo,
                tempo_total,
            )

            return resultado, tempo_total

    finally:
        OCR_LOCK.release()


def executar_ocr_bytes(conteudo: bytes, modo: str = "planilha"):
    if modo not in ("planilha", "nota"):
        raise ValueError("Modo OCR inválido.")

    return _executar_processo(conteudo, modo)


def executar_pdf_bytes(conteudo: bytes):
    # Executa a extração textual e estruturação do PDF em subprocesso.
    return _executar_processo(conteudo, "pdf")
