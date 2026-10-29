"""
Serviço auxiliar para gerenciamento e otimização de arquivos de comprovantes.
- Comprime imagens (Pillow) convertendo para JPEG otimizado ou redimensionando se excessivamente grandes
- Remove arquivos físicos associados a comprovantes ao excluir despesas ou receitas
- Remove arquivos temporários de preview/IA
"""
import os
import uuid
import logging
from PIL import Image, ImageOps
from flask import current_app

logger = logging.getLogger(__name__)

EXTENSOES_PERMITIDAS = {'png', 'jpg', 'jpeg', 'webp', 'pdf'}
LADO_MAXIMO = 1600
QUALIDADE_JPEG = 80


def obter_pasta_comprovantes() -> str:
    pasta = current_app.config.get('UPLOAD_COMPROVANTES_DIR')
    if not pasta:
        base_dir = current_app.config.get('BASE_DIR', os.getcwd())
        pasta = os.path.join(base_dir, 'uploads', 'comprovantes')
    os.makedirs(pasta, exist_ok=True)
    return pasta


def salvar_e_otimizar_comprovante(file_storage) -> str | None:
    """
    Salva o arquivo recebido via request.files de forma otimizada.
    Se for imagem, ajusta rotação EXIF, redimensiona se maior que 1600px e comprime com qualidade balanceada.
    Se for PDF, salva diretamente.
    Retorna o nome do arquivo gerado (ex: uuid.jpeg) ou None se inválido.
    """
    if not file_storage or not file_storage.filename:
        return None

    nome_orig = file_storage.filename
    ext = nome_orig.rsplit('.', 1)[-1].lower() if '.' in nome_orig else ''
    if ext not in EXTENSOES_PERMITIDAS:
        return None

    pasta_destino = obter_pasta_comprovantes()
    token = str(uuid.uuid4())

    # Se for PDF, grava direto sem alteração
    if ext == 'pdf':
        nome_final = f"{token}.pdf"
        caminho_final = os.path.join(pasta_destino, nome_final)
        file_storage.save(caminho_final)
        return nome_final

    # Se for imagem, otimiza com Pillow
    try:
        img = Image.open(file_storage)
        # Respeita orientação EXIF (fotos de celular)
        img = ImageOps.exif_transpose(img)

        # Converte RGBA para RGB se salvar em JPEG
        if img.mode in ('RGBA', 'LA', 'P'):
            rgb_img = Image.new('RGB', img.size, (255, 255, 255))
            rgb_img.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
            img = rgb_img
        elif img.mode != 'RGB':
            img = img.convert('RGB')

        # Redimensiona mantendo proporção caso seja enorme
        w, h = img.size
        if max(w, h) > LADO_MAXIMO:
            fator = LADO_MAXIMO / float(max(w, h))
            novo_tamanho = (int(w * fator), int(h * fator))
            img = img.resize(novo_tamanho, Image.Resampling.LANCZOS)

        nome_final = f"{token}.jpeg"
        caminho_final = os.path.join(pasta_destino, nome_final)
        img.save(caminho_final, format='JPEG', optimize=True, quality=QUALIDADE_JPEG)
        logger.info(f"Comprovante otimizado salvo: {nome_final}")
        return nome_final
    except Exception as e:
        logger.warning(f"Falha ao otimizar imagem com Pillow ({e}). Salvando arquivo original.")
        # Fallback para salvar arquivo bruto
        file_storage.seek(0)
        nome_final = f"{token}.{ext}"
        caminho_final = os.path.join(pasta_destino, nome_final)
        file_storage.save(caminho_final)
        return nome_final


def promover_temporario_para_comprovante(caminho_origem: str, ext: str) -> str | None:
    """
    Copia e otimiza um arquivo temporário (ex: de tmp_ia) para a pasta permanente de comprovantes.
    """
    if not os.path.isfile(caminho_origem):
        return None

    pasta_destino = obter_pasta_comprovantes()
    token = str(uuid.uuid4())
    ext = ext.lower().replace('.', '')

    if ext == 'pdf':
        nome_final = f"{token}.pdf"
        caminho_final = os.path.join(pasta_destino, nome_final)
        try:
            import shutil
            shutil.copy2(caminho_origem, caminho_final)
            return nome_final
        except Exception as e:
            logger.error(f"Erro ao copiar PDF temporário: {e}")
            return None

    try:
        with Image.open(caminho_origem) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode in ('RGBA', 'LA', 'P'):
                rgb_img = Image.new('RGB', img.size, (255, 255, 255))
                rgb_img.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
                img = rgb_img
            elif img.mode != 'RGB':
                img = img.convert('RGB')

            w, h = img.size
            if max(w, h) > LADO_MAXIMO:
                fator = LADO_MAXIMO / float(max(w, h))
                img = img.resize((int(w * fator), int(h * fator)), Image.Resampling.LANCZOS)

            nome_final = f"{token}.jpeg"
            caminho_final = os.path.join(pasta_destino, nome_final)
            img.save(caminho_final, format='JPEG', optimize=True, quality=QUALIDADE_JPEG)
            return nome_final
    except Exception as e:
        logger.warning(f"Falha ao otimizar temporário ({e}). Copiando diretamente.")
        try:
            import shutil
            nome_final = f"{token}.{ext}"
            shutil.copy2(caminho_origem, os.path.join(pasta_destino, nome_final))
            return nome_final
        except Exception as e2:
            logger.error(f"Erro ao salvar arquivo comprovante: {e2}")
            return None


def remover_arquivo_comprovante(nome_arquivo: str | None) -> bool:
    """
    Remove com segurança o arquivo físico da pasta uploads/comprovantes.
    Não remove se outro registro ainda estiver referenciando o mesmo arquivo
    (por exemplo, compras parceladas compartilhando o mesmo comprovante).
    """
    if not nome_arquivo:
        return False

    safe_name = os.path.basename(nome_arquivo)

    # Verifica se algum outro gasto ou receita ainda referencia esse arquivo
    from app.models.gasto import Gasto
    from app.models.receita import Receita
    
    outros_gastos = Gasto.query.filter_by(comprovante=safe_name).count()
    outras_receitas = Receita.query.filter_by(comprovante=safe_name).count()

    # Se ainda houver 1 ou mais referências ativas após o registro atual, não deleta
    # Nota: Deve ser chamado antes do commit se filtrado pelo ID, ou verificado se count <= 1
    if outros_gastos + outras_receitas > 1:
        logger.info(f"Arquivo {safe_name} ainda compartilhado por outros registros. Mantido no disco.")
        return False

    pasta = obter_pasta_comprovantes()
    caminho = os.path.join(pasta, safe_name)
    if os.path.isfile(caminho):
        try:
            os.remove(caminho)
            logger.info(f"Arquivo físico de comprovante removido: {safe_name}")
            return True
        except OSError as e:
            logger.warning(f"Não foi possível remover arquivo físico {caminho}: {e}")
    return False
