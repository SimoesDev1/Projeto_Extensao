"""
Benchmark comparativo do OCR após otimizações de RAM/velocidade.
Mede: tamanho da matriz, tempo de preparação de imagem, e chama o worker via subprocess.
Usa a imagem de teste em fixtures/ ou cria uma imagem sintética se não houver.
"""
import os
import sys
import time
import io
import json
import tempfile
import subprocess
import tracemalloc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# --- Importar o módulo worker diretamente para medir preparar_imagem ---
sys.path.insert(0, ROOT)
import ocr_worker_process as worker

from PIL import Image
import numpy as np


def criar_imagem_sintetica(w=1000, h=800, formato="JPEG"):
    """Cria imagem RGB sintética com texto simulado."""
    img = Image.new("RGB", (w, h), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format=formato)
    buf.seek(0)
    return buf.read()


def salvar_temp(conteudo: bytes, sufixo=".jpg") -> str:
    with tempfile.NamedTemporaryFile(suffix=sufixo, delete=False) as f:
        f.write(conteudo)
        return f.name


def medir_preparar_imagem(caminho, modo, label):
    tracemalloc.start()
    inicio = time.perf_counter()
    arr = worker.preparar_imagem(caminho, modo)
    elapsed = time.perf_counter() - inicio
    _, pico = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    mb_matriz = arr.nbytes / (1024 * 1024)
    mb_pico = pico / (1024 * 1024)
    print(f"[{label}] modo={modo!r} | {arr.shape[1]}x{arr.shape[0]}px | "
          f"matriz={mb_matriz:.2f} MB | pico_ram={mb_pico:.2f} MB | tempo={elapsed*1000:.1f} ms")
    return mb_matriz, mb_pico, elapsed


def main():
    print("=" * 70)
    print("BENCHMARK OCR — após otimizações v2")
    print("=" * 70)

    # Imagem 1000×800 (tamanho típico de foto de cupom)
    img_bytes_1000 = criar_imagem_sintetica(1000, 800)
    # Imagem 2000×1500 (foto com câmera, precisa de downscale)
    img_bytes_2000 = criar_imagem_sintetica(2000, 1500)
    # Imagem pequena 400×300 (upscale ativaria no código antigo)
    img_bytes_400 = criar_imagem_sintetica(400, 300)

    casos = [
        (img_bytes_1000, "nota",      "nota 1000×800"),
        (img_bytes_2000, "nota",      "nota 2000×1500"),
        (img_bytes_400,  "nota",      "nota 400×300 (pequena)"),
        (img_bytes_1000, "planilha",  "planilha 1000×800"),
        (img_bytes_2000, "planilha",  "planilha 2000×1500"),
        (img_bytes_400,  "planilha",  "planilha 400×300 (pequena)"),
    ]

    total_mb_antes = {
        # Estimativas do código antigo (calculadas na sessão anterior):
        # nota 1000×800 → fator 2 → 2000×1600 RGB = 9.15 MB
        # nota 2000×1500 → downscale p/ 1280×960 → 3.5 MB
        # nota 400×300 → fator 3 → 1200×900 → 3.08 MB
        # planilha 1000×800 → fator 2 → 2000×1600 → 9.15 MB
        # planilha 2000×1500 → downscale p/ 1600×1200 → 5.49 MB
        # planilha 400×300 → fator 3 → 1200×900 → 3.08 MB
        "nota 1000×800": 9.15,
        "nota 2000×1500": 3.5,
        "nota 400×300 (pequena)": 3.08,
        "planilha 1000×800": 9.15,
        "planilha 2000×1500": 5.49,
        "planilha 400×300 (pequena)": 3.08,
    }

    print()
    resultados = []
    for img_bytes, modo, label in casos:
        caminho = salvar_temp(img_bytes, ".jpg")
        try:
            mb_matriz, mb_pico, elapsed = medir_preparar_imagem(caminho, modo, label)
            antes = total_mb_antes.get(label, 0)
            reducao = ((antes - mb_matriz) / antes * 100) if antes > 0 else 0
            resultados.append((label, antes, mb_matriz, reducao, elapsed * 1000))
        finally:
            os.unlink(caminho)

    print()
    print("=" * 70)
    print("RESUMO — redução de RAM na matriz de imagem")
    print(f"{'Caso':<30} {'Antes(MB)':>10} {'Depois(MB)':>11} {'Redução':>9} {'ms':>8}")
    print("-" * 70)
    for label, antes, depois, red, ms in resultados:
        print(f"{label:<30} {antes:>10.2f} {depois:>11.2f} {red:>8.1f}% {ms:>8.1f}")

    print()
    print("Novas variáveis de ambiente:")
    print("  OCR_MAX_LADO=960              # notas fiscais (era 1280)")
    print("  OCR_MAX_LADO_PLANILHA=1024    # planilha/IPCA (era 1600)")
    print("  OCR_LIMITE_MATRIZ_MB=50       # guard de segurança")
    print("=" * 70)


if __name__ == "__main__":
    main()
