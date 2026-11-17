from pathlib import Path
from datetime import datetime
import re
import shutil
import sys

arquivos = [
    Path("app/services/planilha_produto_service.py"),
    Path("extrator_nota.py"),
    Path("app/services/ai_fiscal_service.py"),
    Path("run.py"),
]

for arquivo in arquivos:
    if not arquivo.is_file():
        raise SystemExit(f"Arquivo obrigatório não encontrado: {arquivo}")

def substituir_unico(texto, padrao, novo, nome):
    resultado, quantidade = re.subn(
        padrao, novo, texto, count=1, flags=re.MULTILINE | re.DOTALL
    )
    if quantidade != 1:
        raise RuntimeError(
            f"Não foi possível localizar exatamente uma ocorrência de: {nome}"
        )
    return resultado

# Lê e transforma tudo em memória antes de gravar qualquer arquivo.
conteudos = {
    arquivo: arquivo.read_text(encoding="utf-8")
    for arquivo in arquivos
}

# ============================================================
# 1. SERVIÇO DE PLANILHAS
# ============================================================
p = arquivos[0]
s = conteudos[p]

s = substituir_unico(
    s,
    r"^import threading as _threading\s*\n.*?^# Cache LRU",
    """import threading as _threading
import hashlib
from collections import OrderedDict

from app.services.ocr_worker_service import (
    executar_ocr_bytes,
    OCROcupadoError,
)

# Cache LRU""",
    "globais e bloqueios antigos do OCR na planilha",
)

s = substituir_unico(
    s,
    r"^def _obter_ocr_engine\(\):.*?(?=^def _registrar_memoria_ocr)",
    "",
    "funções antigas de execução OCR",
)

s = substituir_unico(
    s,
    r"^def _criar_ocr_engine\(\):.*?(?=^class NumberedCanvas)",
    "",
    "inicialização antiga do RapidOCR",
)

s = substituir_unico(
    s,
    r"^    @classmethod\s*\n    def _preparar_imagem_para_ocr\(cls, conteudo: bytes\):.*?(?=^    @classmethod\s*\n    def ler_arquivo_imagem)",
    "",
    "preparação de imagem dentro do processo Flask",
)

s = substituir_unico(
    s,
    r"        if not _OCR_PIPELINE_LOCK\.acquire\(blocking=False\):.*?(?=        if not res:)",
    """        inicio_ocr = time.perf_counter()

        # A preparação da imagem e o modelo ficam no processo independente.
        res, tempo_ocr = executar_ocr_bytes(conteudo, modo="planilha")

        logger.info(
            "OCR | Planilha processada pelo worker | "
            "caixas=%d | tempo_total=%.2fs | tempo_chamada=%.2fs",
            len(res or []),
            tempo_ocr,
            time.perf_counter() - inicio_ocr,
        )

""",
    "bloqueio, preparação e inferência da leitura de planilhas",
)

conteudos[p] = s

# ============================================================
# 2. EXTRATOR DE NOTAS
# ============================================================
p = arquivos[1]
s = conteudos[p]

s = substituir_unico(
    s,
    r"^def _obter_ocr_engine\(\):.*?(?=^def _obter_ocr_cache)",
    "",
    "wrappers antigos do motor OCR",
)

inicio_funcao = s.find("def _imagem_bytes_para_texto_ocr(image_bytes: bytes) -> str:")
if inicio_funcao < 0:
    raise RuntimeError("Função _imagem_bytes_para_texto_ocr não encontrada.")

marcador = '    if not res:\n        logger.warning("OCR | Nenhum texto detectado")'
fim_prefixo = s.find(marcador, inicio_funcao)
if fim_prefixo < 0:
    raise RuntimeError("Trecho de pós-processamento do OCR não encontrado.")

novo_inicio = '''def _imagem_bytes_para_texto_ocr(image_bytes: bytes) -> str:
    """
    Executa OCR de imagem em processo independente e organiza as detecções.
    O pós-processamento original é preservado abaixo.
    """
    inicio_total = time.perf_counter()

    logger.info(
        "OCR | Enviando imagem ao processo independente | Tamanho: %.2f MB",
        len(image_bytes) / (1024 * 1024),
    )

    from app.services.ocr_worker_service import executar_ocr_bytes

    res, tempo_worker = executar_ocr_bytes(image_bytes, modo="nota")

    logger.info(
        "OCR | Worker concluído | caixas=%d | tempo_worker=%.2fs",
        len(res or []),
        tempo_worker,
    )

'''
s = s[:inicio_funcao] + novo_inicio + s[fim_prefixo:]

s = substituir_unico(
    s,
    r"    from app\.services\.planilha_produto_service import \(\s*_OCR_PIPELINE_LOCK,\s*OCROcupadoError,\s*\)\s*\n\s*if not _OCR_PIPELINE_LOCK\.acquire\(blocking=False\):.*?    # Diagn",
    """    texto_completo = _imagem_bytes_para_texto_ocr(image_bytes)

    # Diagn""",
    "bloqueio antigo do extrator de notas",
)

conteudos[p] = s

# ============================================================
# 3. SERVIÇO FISCAL
# ============================================================
p = arquivos[2]
s = conteudos[p]

s = substituir_unico(
    s,
    r"        import time as _time\s*\n        from concurrent\.futures import ThreadPoolExecutor, TimeoutError as _FutTimeout\s*\n\s*TIMEOUT_S = min\(int\(os\.getenv\(\"EXTRACAO_TIMEOUT\", \"90\"\)\), 95\)\s*\n        _t0 = _time\.perf_counter\(\)\s*\n\s*from app\.services\.planilha_produto_service import OCROcupadoError",
    """        import time as _time

        TIMEOUT_S = max(
            15,
            min(
                int(os.getenv(
                    "OCR_PROCESS_TIMEOUT",
                    os.getenv("EXTRACAO_TIMEOUT", "75"),
                )),
                100,
            ),
        )
        _t0 = _time.perf_counter()

        from app.services.ocr_worker_service import (
            OCROcupadoError,
            OCRTimeoutError,
        )""",
    "imports e configuração de timeout do serviço fiscal",
)

s = substituir_unico(
    s,
    r"            # Executa em thread separada para limitar a espera do worker\..*?(?=            if dados_extrator is None:)",
    """            # PDFs continuam usando extração textual.
            # Imagens usam processo independente com encerramento real por timeout.
            dados_extrator = _chamar_extrator()

""",
    "ThreadPoolExecutor que não encerrava o OCR",
)

s = substituir_unico(
    s,
    r"        except OCROcupadoError:",
    """        except OCRTimeoutError:
            logger.error(
                "EXTRATOR | OCR excedeu o limite de %ds | MIME: %s | Bytes: %d",
                TIMEOUT_S,
                mimetype,
                len(file_bytes),
            )
            return cls._gerar_resultado_manual(
                f"Tempo limite de OCR excedido ({TIMEOUT_S}s). "
                "Preencha os dados manualmente.",
                hoje_str,
            )

        except OCROcupadoError:""",
    "tratamento de timeout do processo OCR",
)

conteudos[p] = s

# ============================================================
# 4. INICIALIZAÇÃO DA APLICAÇÃO
# ============================================================
p = arquivos[3]
s = conteudos[p]

s = substituir_unico(
    s,
    r"^def _aquecer_ocr\(\):.*?(?=^registrar_versoes_ocr\(\))",
    "",
    "função de aquecimento do OCR",
)

s = substituir_unico(
    s,
    r"^# Por padrão, não inicializa o modelo OCR na partida\..*?^    threading\.Thread\(target=_aquecer_ocr, daemon=True, name=\"ocr-warmup\"\)\.start\(\)\s*\n?",
    "",
    "inicialização opcional do modelo OCR no Flask",
)

conteudos[p] = s

# ============================================================
# 5. VERIFICAÇÕES PRÉ-GRAVAÇÃO
# ============================================================
proibidos = {
    arquivos[0]: [
        "_OCR_PIPELINE_LOCK",
        "_OCR_ENGINE",
        "_obter_ocr_engine",
        "_executar_ocr(",
        "_criar_ocr_engine",
        "rapidocr_onnxruntime",
    ],
    arquivos[1]: [
        "_OCR_PIPELINE_LOCK",
        "_obter_ocr_engine",
        "_executar_ocr(",
        "rapidocr_onnxruntime",
    ],
    arquivos[2]: [
        "ThreadPoolExecutor",
        "_FutTimeout",
    ],
    arquivos[3]: [
        "_aquecer_ocr",
        "_obter_ocr_engine",
    ],
}

for arquivo, termos in proibidos.items():
    for termo in termos:
        if termo in conteudos[arquivo]:
            raise RuntimeError(
                f"Referência antiga ainda presente em {arquivo}: {termo}"
            )

# Confirma que os novos arquivos de worker existem.
for arquivo in (
    Path("app/services/ocr_worker_service.py"),
    Path("ocr_worker_process.py"),
):
    if not arquivo.is_file():
        raise RuntimeError(f"Worker ausente: {arquivo}")

# ============================================================
# 6. BACKUPS E GRAVAÇÃO
# ============================================================
sufixo = datetime.now().strftime("%Y%m%d_%H%M%S")
backups = []

for arquivo in arquivos:
    backup = arquivo.with_name(
        arquivo.name + f".bak_integracao_worker_{sufixo}"
    )
    shutil.copy2(arquivo, backup)
    backups.append(backup)

for arquivo in arquivos:
    arquivo.write_text(conteudos[arquivo], encoding="utf-8", newline="\n")

print("\nINTEGRAÇÃO APLICADA.")
print("\nBackups criados:")
for backup in backups:
    print(f"  {backup}")

print("\nArquivos modificados:")
for arquivo in arquivos:
    print(f"  {arquivo}")
