import argparse
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
