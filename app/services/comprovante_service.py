
"""
Serviço auxiliar para gerenciamento e otimização de arquivos de comprovantes.

- Comprime imagens e converte para JPEG otimizado.
- Redimensiona imagens excessivamente grandes.
- Remove arquivos físicos sem apagar comprovantes ainda utilizados.
- Promove arquivos temporários para a pasta permanente.
"""

import logging
import os
import shutil
import uuid

from PIL import Image, ImageOps
from flask import current_app

logger = logging.getLogger(__name__)

EXTENSOES_PERMITIDAS = {"png", "jpg", "jpeg", "webp", "pdf"}
LADO_MAXIMO = 1600
QUALIDADE_JPEG = 80


def obter_pasta_comprovantes() -> str:
    pasta = current_app.config.get("UPLOAD_COMPROVANTES_DIR")

    if not pasta:
        base_dir = current_app.config.get("BASE_DIR", os.getcwd())
        pasta = os.path.join(base_dir, "uploads", "comprovantes")

    os.makedirs(pasta, exist_ok=True)
    return pasta


def _converter_para_rgb(img: Image.Image) -> Image.Image:
    """Converte imagens com transparência para RGB com fundo branco."""
    if img.mode in ("RGBA", "LA"):
        imagem_rgb = Image.new("RGB", img.size, (255, 255, 255))
        imagem_rgb.paste(img, mask=img.getchannel("A"))
        return imagem_rgb

    if img.mode == "P":
        imagem_rgba = img.convert("RGBA")
        imagem_rgb = Image.new("RGB", img.size, (255, 255, 255))
        imagem_rgb.paste(imagem_rgba, mask=imagem_rgba.getchannel("A"))
        return imagem_rgb

    if img.mode != "RGB":
        return img.convert("RGB")

    return img


def _otimizar_imagem(img: Image.Image) -> Image.Image:
    img = ImageOps.exif_transpose(img)
    img = _converter_para_rgb(img)

    largura, altura = img.size
    maior_lado = max(largura, altura)

    if maior_lado > LADO_MAXIMO:
        fator = LADO_MAXIMO / float(maior_lado)
        novo_tamanho = (
            max(1, int(largura * fator)),
            max(1, int(altura * fator))
        )
        img = img.resize(novo_tamanho, Image.Resampling.LANCZOS)

    return img


def salvar_e_otimizar_comprovante(file_storage) -> str | None:
    """Salva um arquivo recebido pelo Flask de forma otimizada."""
    if not file_storage or not file_storage.filename:
        return None

    nome_orig = file_storage.filename
    ext = nome_orig.rsplit(".", 1)[-1].lower() if "." in nome_orig else ""

    if ext not in EXTENSOES_PERMITIDAS:
        return None

    pasta_destino = obter_pasta_comprovantes()
    token = str(uuid.uuid4())

    if ext == "pdf":
        nome_final = f"{token}.pdf"
        caminho_final = os.path.join(pasta_destino, nome_final)

        try:
            file_storage.save(caminho_final)
            return nome_final
        except Exception:
            logger.exception("Erro ao salvar comprovante PDF.")
            return None

    try:
        file_storage.stream.seek(0)

        with Image.open(file_storage.stream) as imagem:
            img = _otimizar_imagem(imagem)
            nome_final = f"{token}.jpeg"
            caminho_final = os.path.join(pasta_destino, nome_final)

            img.save(
                caminho_final,
                format="JPEG",
                optimize=True,
                quality=QUALIDADE_JPEG
            )

        logger.info("Comprovante otimizado salvo: %s", nome_final)
        return nome_final

    except Exception:
        logger.exception("Falha ao otimizar imagem. Tentando salvar original.")

        try:
            file_storage.stream.seek(0)
            nome_final = f"{token}.{ext}"
            caminho_final = os.path.join(pasta_destino, nome_final)
            file_storage.save(caminho_final)
            return nome_final
        except Exception:
            logger.exception("Não foi possível salvar o comprovante.")
            return None


def promover_temporario_para_comprovante(
    caminho_origem: str,
    ext: str
) -> str | None:
    """Copia e otimiza um arquivo temporário para a pasta permanente."""

    if not caminho_origem or not os.path.isfile(caminho_origem):
        return None

    ext = (ext or "").lower().replace(".", "")

    if ext not in EXTENSOES_PERMITIDAS:
        logger.warning("Extensão não permitida na promoção: %s", ext)
        return None

    pasta_destino = obter_pasta_comprovantes()
    token = str(uuid.uuid4())

    if ext == "pdf":
        nome_final = f"{token}.pdf"
        caminho_final = os.path.join(pasta_destino, nome_final)

        try:
            shutil.copy2(caminho_origem, caminho_final)
            return nome_final
        except Exception:
            logger.exception("Erro ao copiar PDF temporário.")
            return None

    try:
        with Image.open(caminho_origem) as imagem:
            img = _otimizar_imagem(imagem)

            nome_final = f"{token}.jpeg"
            caminho_final = os.path.join(pasta_destino, nome_final)

            img.save(
                caminho_final,
                format="JPEG",
                optimize=True,
                quality=QUALIDADE_JPEG
            )

        return nome_final

    except Exception:
        logger.exception("Falha ao otimizar imagem temporária.")

        try:
            nome_final = f"{token}.{ext}"
            caminho_final = os.path.join(pasta_destino, nome_final)
            shutil.copy2(caminho_origem, caminho_final)
            return nome_final
        except Exception:
            logger.exception("Erro ao copiar comprovante temporário.")
            return None


def remover_arquivo_comprovante(nome_arquivo: str | None) -> bool:
    """
    Remove o arquivo físico somente se nenhum gasto ou receita
    continuar referenciando o comprovante.

    Deve ser chamada depois do commit da exclusão do registro.
    """
    if not nome_arquivo:
        return False

    safe_name = os.path.basename(nome_arquivo)

    if safe_name != nome_arquivo or safe_name in ("", ".", ".."):
        logger.warning("Nome de comprovante inválido: %r", nome_arquivo)
        return False

    try:
        from app.models.gasto import Gasto
        from app.models.receita import Receita

        outros_gastos = Gasto.query.filter_by(
            comprovante=safe_name
        ).count()

        outras_receitas = Receita.query.filter_by(
            comprovante=safe_name
        ).count()

        # A exclusão já foi confirmada no banco. Qualquer referência
        # restante significa que o arquivo ainda está em uso.
        if outros_gastos + outras_receitas > 0:
            logger.info(
                "Comprovante %s ainda está sendo utilizado. Mantido.",
                safe_name
            )
            return False

        pasta = obter_pasta_comprovantes()
        caminho = os.path.join(pasta, safe_name)

        if not os.path.isfile(caminho):
            return False

        os.remove(caminho)
        logger.info("Comprovante removido: %s", safe_name)
        return True

    except Exception:
        logger.exception(
            "Erro ao verificar ou remover comprovante: %s",
            safe_name
        )
        return False
