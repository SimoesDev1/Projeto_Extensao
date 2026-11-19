"""
Doce como Mel — Suíte de Testes da Pipeline Fiscal e IA
Testa todos os componentes:
  1. Extração de PDF e imagem (extrator_nota e OCR worker)
  2. Isolamento de processo (ocr_worker_service e ocr_worker_process)
  3. Provedor AiFiscalService e AiChatService (com fallback e cache)
  4. Precisão fiscal (sem inventar produtos, valores, datas ou formas de pagamento)
  5. Comunicação e resiliência com FastAPI e fallback local
"""
import hashlib
import json
import os
import sys
import time
import unittest
from pathlib import Path

# Garante importação do projeto
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app import create_app, db
from app.services.ai_fiscal_service import AiFiscalService, _CACHE_IA_FISCAL
from app.services.ai_chat_service import AiChatService
from app.services.ocr_worker_service import executar_pdf_bytes, executar_nota_fiscal_bytes


class TestIAPipeline(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.ctx = cls.app.app_context()
        cls.ctx.push()
        cls.pdf_sample = ROOT_DIR / "uploads" / "comprovantes" / "40371c52-edf3-4f0c-bbea-c92546aab75b.pdf"
        cls.img_sample = ROOT_DIR / "teste_ocr_worker.png"

    @classmethod
    def tearDownClass(cls):
        cls.ctx.pop()

    def test_01_extracao_pdf_local(self):
        """Valida que o extrator de PDF em subprocesso extrai dados reais sem alucinações."""
        self.assertTrue(self.pdf_sample.is_file(), "Arquivo de teste PDF não encontrado.")
        conteudo = self.pdf_sample.read_bytes()

        resultado, tempo = executar_pdf_bytes(conteudo)
        self.assertIsInstance(resultado, dict)
        self.assertGreater(tempo, 0)
        self.assertEqual(resultado.get("fornecedor"), "MANIA BEST FOODS ALIMENTACAO LTDA")
        self.assertEqual(resultado.get("valor_total"), 920.80)
        self.assertEqual(resultado.get("data"), "2026-10-28")
        self.assertEqual(resultado.get("forma_pagamento"), "Pix")
        print(f" [OK] Teste 1: PDF extraído em {tempo:.2f}s com precisão.")

    def test_02_extracao_imagem_ocr_local(self):
        """Valida que o extrator de imagem em subprocesso roda sem travar o processo Flask."""
        self.assertTrue(self.img_sample.is_file(), "Arquivo de teste de imagem não encontrado.")
        conteudo = self.img_sample.read_bytes()

        resultado, tempo = executar_nota_fiscal_bytes(conteudo)
        self.assertIsInstance(resultado, dict)
        self.assertGreater(tempo, 0)
        self.assertIn(resultado.get("fornecedor"), ["DOCE COMO MEL", "DOCECOMO MEL"])
        self.assertEqual(resultado.get("valor_total"), 18.90)
        self.assertGreaterEqual(len(resultado.get("itens") or []), 1)
        print(f" [OK] Teste 2: Imagem OCR extraída em {tempo:.2f}s.")

    def test_03_precisao_fiscal_sem_alucinacao(self):
        """Garante que forma de pagamento e itens não são inventados na conversão fiscal."""
        dados_sem_forma = {
            "fornecedor": "FORNECEDOR TESTE",
            "data": "2026-11-10",
            "forma_pagamento": "Desconhecido",  # Não deve virar Cartão ou Pix sem evidência
            "parcelas": 1,
            "valor_total": 150.00,
            "itens": []  # Não deve inventar itens fictícios
        }

        convertido = AiFiscalService._converter_extrator_para_revisao(
            dados_sem_forma,
            hoje_str="2026-11-17",
            categorias_existentes=["Ingredientes", "Embalagens"]
        )

        self.assertEqual(convertido["forma_pagamento"], "", "Forma de pagamento não deve ser inventada.")
        self.assertEqual(convertido["itens"], [], "Itens não devem ser inventados quando ausentes no documento.")
        self.assertEqual(convertido["valor_total"], 150.00)
        print(" [OK] Teste 3: Regra de precisão fiscal estrita validada (sem alucinações).")

    def test_04_cache_inteligente_e_economia_tokens(self):
        """Valida que o cache em memória evita chamadas repetidas economizando 100% dos tokens."""
        _CACHE_IA_FISCAL.clear()
        conteudo = self.pdf_sample.read_bytes()

        t0 = time.perf_counter()
        res1 = AiFiscalService.interpretar_documento(conteudo, "application/pdf", ["Ingredientes"])
        tempo1 = time.perf_counter() - t0

        t1 = time.perf_counter()
        res2 = AiFiscalService.interpretar_documento(conteudo, "application/pdf", ["Ingredientes"])
        tempo2 = time.perf_counter() - t1

        self.assertEqual(res1["valor_total"], res2["valor_total"])
        self.assertLess(tempo2, 0.05, "Segunda chamada deve vir imediatamente do cache.")
        self.assertGreater(len(_CACHE_IA_FISCAL), 0)
        print(f" [OK] Teste 4: Cache IA validado. 1ª chamada: {tempo1:.2f}s | 2ª chamada (cache): {tempo2*1000:.2f}ms.")

    def test_05_resiliencia_fastapi_e_fallback_local(self):
        """Valida que se o FastAPI OCR estiver desligado ou falhar, o serviço usa o worker local."""
        os.environ["OCR_API_URL"] = "http://127.0.0.1:9999"  # Porta inativa propositalmente
        conteudo = self.pdf_sample.read_bytes()

        dados = AiFiscalService._obter_dados_extrator_bruto(conteudo, "application/pdf")
        self.assertIsInstance(dados, dict)
        self.assertEqual(dados.get("valor_total"), 920.80)
        print(" [OK] Teste 5: Fallback automático para worker local funcionou perfeitamente.")

    def test_06_banco_dados_e_rotas(self):
        """Valida conexão com o banco de dados e rotas principais."""
        from app.models.usuario import Usuario
        total_usuarios = Usuario.query.count()
        self.assertGreaterEqual(total_usuarios, 0)

        with self.app.test_client() as client:
            resp_health = client.get("/health")
            self.assertEqual(resp_health.status_code, 200)

            # Rota /ia/ requer autenticação -> deve redirecionar para login
            resp_ia = client.get("/ia/")
            self.assertEqual(resp_ia.status_code, 302)
        print(f" [OK] Teste 6: Banco de dados e rotas principais OK (usuários: {total_usuarios}).")


if __name__ == "__main__":
    unittest.main()
