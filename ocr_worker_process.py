import argparse
import gc
import json
import logging
import os
import sys
import time
from pathlib import Path

# Garante que o worker saiba que já é um processo isolado
os.environ["DENTRO_DO_WORKER"] = "1"
# Limita alocação de threads e pools nativos para economizar memória no Linux/Render
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"


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
    """
    Prepara a imagem para OCR com consumo mínimo de RAM.

    modo="planilha" (IPCA / listas de preços):
        - max_lado configurável via OCR_MAX_LADO_PLANILHA (padrão 1024, era 1600)
        - upscale máximo 1.5x para evitar matrizes gigantes
        - resize com BILINEAR (2-3× mais rápido que LANCZOS, qualidade ok para OCR)

    modo="nota" / outros (notas fiscais, recibos):
        - max_lado configurável via OCR_MAX_LADO (padrão 960, era 1280)
        - SEM upscale — redimensionamento apenas downscale
        - processamento via grayscale intermediário (economiza 66 % RAM durante resize)
        - BILINEAR em vez de LANCZOS
    """
    import numpy as np
    from PIL import Image, ImageOps

    if modo == "planilha":
        # -----------------------------------------------------------
        # PLANILHA / IPCA  — precisa de resolução um pouco maior para
        # ler textos pequenos em tabelas, mas sem exagero.
        # -----------------------------------------------------------
        max_lado = int(os.getenv("OCR_MAX_LADO_PLANILHA", "1024"))  # era 1600

        with Image.open(caminho) as original:
            img = ImageOps.exif_transpose(original).convert("RGB")

        # Downscale se necessário
        if max(img.size) > max_lado:
            img.thumbnail((max_lado, max_lado), Image.BILINEAR)  # era LANCZOS

        # Upscale suave (máx 1.5×) só quando a imagem for muito pequena
        fator_float = max_lado / max(img.size)
        if fator_float > 1.1:
            fator = min(1.5, fator_float)
            novo_w = int(img.width * fator)
            novo_h = int(img.height * fator)
            img = img.resize((novo_w, novo_h), Image.BILINEAR)

        arr = np.array(img)
        del img
        return arr

    # -----------------------------------------------------------
    # NOTA FISCAL / RECIBO  — apenas downscale, sem upscale.
    # Imagens de recibo já chegam em resolução suficiente para OCR.
    # -----------------------------------------------------------
    lado_max = int(os.getenv("OCR_MAX_LADO", "960"))  # era 1280

    with Image.open(caminho) as original:
        if original.format == "JPEG":
            original.draft("L", (lado_max, lado_max))  # decodifica direto em cinza

        # Grayscale durante o processamento: 1/3 da RAM de um array RGB
        img = ImageOps.exif_transpose(original).convert("L")

    img.thumbnail((lado_max, lado_max), Image.BILINEAR)  # era LANCZOS; sem upscale

    # RapidOCR exige RGB — conversão final é barata (duplica canais)
    arr = np.array(img.convert("RGB"))
    del img
    return arr


def processar_ocr(caminho, modo):
    imagem = preparar_imagem(caminho, modo)

    matriz_mb = imagem.nbytes / (1024 * 1024)
    logging.info(
        "Imagem preparada | dimensões=%sx%s | matriz=%.2f MB",
        imagem.shape[1],
        imagem.shape[0],
        matriz_mb,
    )

    # Guard de segurança: se a matriz ainda for grande demais, reduz na hora
    _LIMITE_MB = float(os.getenv("OCR_LIMITE_MATRIZ_MB", "50"))
    if matriz_mb > _LIMITE_MB:
        from PIL import Image
        import numpy as np
        img_tmp = Image.fromarray(imagem)
        fator_reducao = (_LIMITE_MB / matriz_mb) ** 0.5
        novo_w = max(1, int(img_tmp.width * fator_reducao))
        novo_h = max(1, int(img_tmp.height * fator_reducao))
        img_tmp = img_tmp.resize((novo_w, novo_h), Image.BILINEAR)
        del imagem
        imagem = np.array(img_tmp)
        del img_tmp
        gc.collect()
        logging.warning(
            "Guard ativado: matriz reduzida para %.2f MB (%dx%d)",
            imagem.nbytes / (1024 * 1024),
            imagem.shape[1],
            imagem.shape[0],
        )

    from rapidocr_onnxruntime import RapidOCR

    threads_intra = int(os.getenv("OCR_INTRA_OP_THREADS", "1"))
    threads_inter = int(os.getenv("OCR_INTER_OP_THREADS", "1"))

    motor = RapidOCR(
        use_cls=False,
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
    gc.collect()

    return resposta


def processar_imagem_fiscal(caminho):
    # A extração fiscal acontece exclusivamente no subprocesso.
    import extrator_nota

    conteudo = Path(caminho).read_bytes()

    logging.info(
        "IMAGEM FISCAL | Iniciando extração | bytes=%d",
        len(conteudo),
    )

    resultado = extrator_nota.extrair_dados_imagem_ocr(conteudo, caminho_imagem=caminho)

    if not isinstance(resultado, dict):
        raise ValueError(
            "O extrator de imagem fiscal retornou dados inválidos."
        )

    logging.info(
        "IMAGEM FISCAL | Extração concluída | itens=%d",
        len(resultado.get("itens") or []),
    )

    return converter_json(resultado)


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
        choices=("planilha", "nota", "pdf", "fiscal_imagem"),
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
        elif args.mode == "fiscal_imagem":
            resultado = processar_imagem_fiscal(caminho)
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
