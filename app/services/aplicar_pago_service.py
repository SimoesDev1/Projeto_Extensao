"""
Aplica o campo is_pago no app/services/gasto_service.py.
Uso (dentro de Projeto_Extensão1, com o Flask parado):
    python aplicar_pago_service.py
Cria um backup em app/services/gasto_service.py.bak antes de alterar.
Se algum trecho não for encontrado, nada é gravado.
"""
import shutil
from pathlib import Path

caminho = Path("app/services/gasto_service.py")
texto = caminho.read_text(encoding="utf-8")

trocas = [
    # criar_gasto
    (
        "            id_usuario=dados['id_usuario'],\n"
        "            id_categoria=dados['id_categoria']\n"
        "        )",
        "            id_usuario=dados['id_usuario'],\n"
        "            id_categoria=dados['id_categoria'],\n"
        "            is_pago=dados.get('is_pago', True)\n"
        "        )",
    ),
    # atualizar_gasto
    (
        "        gasto.id_categoria = dados['id_categoria']\n",
        "        gasto.id_categoria = dados['id_categoria']\n"
        "        gasto.is_pago = dados.get('is_pago', gasto.is_pago)\n",
    ),
    # criar_gasto_parcelado (somente a 1ª parcela herda o status)
    (
        "                id_usuario=dados['id_usuario'],\n"
        "                id_categoria=dados['id_categoria']\n"
        "            )",
        "                id_usuario=dados['id_usuario'],\n"
        "                id_categoria=dados['id_categoria'],\n"
        "                is_pago=dados.get('is_pago', True) if i == 1 else False\n"
        "            )",
    ),
]

for antigo, _ in trocas:
    qtd = texto.count(antigo)
    if qtd != 1:
        raise SystemExit(f"Trecho encontrado {qtd}x (esperado 1x), nada foi alterado:\n{antigo}")

shutil.copy(caminho, caminho.with_suffix(".py.bak"))
for antigo, novo in trocas:
    texto = texto.replace(antigo, novo)
caminho.write_text(texto, encoding="utf-8")
print("gasto_service.py atualizado. Backup em gasto_service.py.bak")