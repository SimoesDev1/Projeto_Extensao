from pathlib import Path
from datetime import datetime
import shutil
import py_compile

gerenciador = r"""import json
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


def _obter_timeout():
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

        timeout = _obter_timeout()

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
    # Executa a extra??o textual e estrutura??o do PDF em subprocesso.
    return _executar_processo(conteudo, "pdf")
"""

worker = r"""import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path


def converter_json(valor):
    if valor is None or isinstance(valor, (str, int, float, bool)):
        return valor

    if hasattr(valor, "tolist"):
        return converter_json(valor.tolist())

    if hasattr(valor, "item"):
        return converter_json(valor.item())

    if isinstance(valor, dict):
        return {
            str(chave): converter_json(item)
            for chave, item in valor.items()
        }

    if isinstance(valor, (list, tuple)):
        return [converter_json(item) for item in valor]

    return str(valor)


def preparar_imagem(caminho, modo):
    import numpy as np
    from PIL import Image, ImageOps

    if modo == "planilha":
        max_lado = int(os.getenv("OCR_MAX_LADO", "1600"))

        with Image.open(caminho) as original:
            img = ImageOps.exif_transpose(original).convert("RGB")

        if max(img.size) > max_lado:
            img.thumbnail((max_lado, max_lado), Image.LANCZOS)

        fator = min(3, max(1, round(max_lado / max(img.size))))

        if fator > 1:
            img = img.resize(
                (img.width * fator, img.height * fator),
                Image.LANCZOS,
            )

        return np.array(img)

    lado_max = int(os.getenv("OCR_MAX_LADO", "1280"))
    alvo = int(os.getenv("OCR_ALVO", "1280"))

    with Image.open(caminho) as original:
        if original.format == "JPEG":
            original.draft("RGB", (lado_max, lado_max))

        img = ImageOps.exif_transpose(original).convert("RGB")

    img.thumbnail((lado_max, lado_max))

    fator = min(3, max(1, round(alvo / max(img.size))))

    if fator > 1:
        img = img.resize(
            (img.width * fator, img.height * fator),
            Image.LANCZOS,
        )

    return np.array(img)


def processar_ocr(caminho, modo):
    imagem = preparar_imagem(caminho, modo)

    logging.info(
        "Imagem preparada | dimensões=%sx%s | matriz=%.2f MB",
        imagem.shape[1],
        imagem.shape[0],
        imagem.nbytes / (1024 * 1024),
    )

    from rapidocr_onnxruntime import RapidOCR

    threads_intra = int(os.getenv("OCR_INTRA_OP_THREADS", "1"))
    threads_inter = int(os.getenv("OCR_INTER_OP_THREADS", "1"))

    motor = RapidOCR(
        intra_op_num_threads=threads_intra,
        inter_op_num_threads=threads_inter,
    )

    inicio = time.perf_counter()

    try:
        resultado = motor(imagem, use_cls=False)
    except TypeError:
        resultado = motor(imagem)

    deteccoes = (
        resultado[0]
        if isinstance(resultado, tuple)
        else resultado
    )

    logging.info(
        "OCR concluído | tempo=%.2fs | caixas=%d",
        time.perf_counter() - inicio,
        len(deteccoes or []),
    )

    resposta = converter_json(deteccoes or [])

    del resultado
    del deteccoes
    del motor
    del imagem

    return resposta


def processar_pdf(caminho):
    # Estes imports acontecem exclusivamente no subprocesso.
    import extrator_nota

    conteudo = Path(caminho).read_bytes()

    logging.info(
        "PDF | Iniciando extração textual | bytes=%d",
        len(conteudo),
    )

    resultado = extrator_nota.extrair_dados_pdf(conteudo)

    if not isinstance(resultado, dict):
        raise ValueError(
            "O extrator PDF retornou dados inválidos."
        )

    logging.info(
        "PDF | Extração concluída | itens=%d",
        len(resultado.get("itens") or []),
    )

    return converter_json(resultado)


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--image", required=True)
    parser.add_argument("--output", required=True)

    parser.add_argument(
        "--mode",
        choices=("planilha", "nota", "pdf"),
        required=True,
    )

    args = parser.parse_args()

    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    inicio = time.perf_counter()
    destino = Path(args.output)

    try:
        caminho = os.path.abspath(args.image)

        if not os.path.isfile(caminho):
            raise FileNotFoundError(
                "Arquivo temporário não encontrado."
            )

        if args.mode == "pdf":
            resultado = processar_pdf(caminho)
        else:
            resultado = processar_ocr(caminho, args.mode)

        resposta = {
            "ok": True,
            "resultado": resultado,
        }

        destino.write_text(
            json.dumps(
                resposta,
                ensure_ascii=False,
                allow_nan=False,
            ),
            encoding="utf-8",
        )

        logging.info(
            "Processamento total concluído em %.2fs",
            time.perf_counter() - inicio,
        )

        return 0

    except Exception as exc:
        logging.exception("Falha no processo independente")

        resposta = {
            "ok": False,
            "erro": str(exc),
        }

        destino.write_text(
            json.dumps(resposta, ensure_ascii=False),
            encoding="utf-8",
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(main())
"""

arquivos = {
    Path("app/services/ocr_worker_service.py"): gerenciador,
    Path("ocr_worker_process.py"): worker,
}

# Confere todos os caminhos antes de modificar qualquer arquivo.
for caminho in arquivos:
    if not caminho.is_file():
        raise FileNotFoundError(f"Arquivo não encontrado: {caminho}")

# Valida a compilação em memória antes de criar backups ou escrever.
for caminho, conteudo in arquivos.items():
    compile(conteudo, str(caminho), "exec")

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

for caminho, conteudo in arquivos.items():
    backup = caminho.with_name(
        caminho.name + f".bak_pdf_{timestamp}"
    )

    shutil.copy2(caminho, backup)
    print(f"Backup criado: {backup}")

    caminho.write_text(
        conteudo,
        encoding="utf-8",
        newline="\n",
    )

    print(f"Arquivo atualizado: {caminho}")

# Validação real dos arquivos gravados.
for caminho in arquivos:
    py_compile.compile(str(caminho), doraise=True)
    print(f"Compilação aprovada: {caminho}")

print("\nATUALIZAÇÃO DOS WORKERS CONCLUÍDA.")
