"""
AiChatService — assistente financeiro conversacional com suporte a Gemini e Ollama.
"""
import logging
import os
import re
from datetime import date

import requests

logger = logging.getLogger(__name__)


class AiChatService:
    OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    OLLAMA_MODEL = os.getenv("OLLAMA_CHAT_MODEL", os.getenv("OLLAMA_MODEL", "qwen3-vl:4b-instruct"))
    GEMINI_MODEL = os.getenv("GEMINI_CHAT_MODEL", os.getenv("GEMINI_MODEL", "gemini-1.5-flash"))
    TIMEOUT = min(int(os.getenv("OLLAMA_TIMEOUT", "90")), 95)

    @classmethod
    def is_gemini_configurado(cls) -> bool:
        chave = os.getenv("GEMINI_API_KEY", "").strip()
        return bool(chave and not chave.startswith("AQ.placeholder"))

    @classmethod
    def is_configurado(cls) -> bool:
        if cls.is_gemini_configurado():
            return True
        try:
            r = requests.get(f"{cls.OLLAMA_HOST}/api/tags", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    @classmethod
    def responder(cls, pergunta: str, contexto_financeiro: dict) -> str:
        """
        Envia uma pergunta com contexto financeiro para o modelo e retorna a resposta.
        Prioriza Gemini Flash (baixo consumo e alta velocidade); caso indisponível, usa Ollama.
        """
        mes = contexto_financeiro.get('mes', date.today().month)
        ano = contexto_financeiro.get('ano', date.today().year)
        nome = contexto_financeiro.get('nome_usuario', 'usuária')
        total_rec = contexto_financeiro.get('total_receitas', 0)
        total_desp = contexto_financeiro.get('total_despesas', 0)
        saldo = contexto_financeiro.get('saldo', 0)
        por_cat = contexto_financeiro.get('por_categoria', {})
        por_tipo = contexto_financeiro.get('por_tipo_receita', {})

        meses_nomes = ['Janeiro','Fevereiro','Março','Abril','Maio','Junho',
                       'Julho','Agosto','Setembro','Outubro','Novembro','Dezembro']
        mes_nome = meses_nomes[mes - 1] if 1 <= mes <= 12 else str(mes)

        cat_str = "\n".join(f"  • {cat}: R$ {val:.2f}" for cat, val in por_cat.items()) or "  (sem dados)"
        tipo_str = "\n".join(f"  • {t}: R$ {val:.2f}" for t, val in por_tipo.items()) or "  (sem dados)"

        system_prompt = f"""Você é a assistente financeira do sistema "Doce como Mel", um software de gestão financeira para pequenos negócios de confeitaria e doces.

DADOS FINANCEIROS DE {mes_nome.upper()}/{ano} — {nome}:
- Receitas totais: R$ {total_rec:.2f}
- Despesas totais: R$ {total_desp:.2f}
- Saldo do período: R$ {saldo:.2f}

Despesas por categoria:
{cat_str}

Receitas por tipo:
{tipo_str}

INSTRUÇÕES:
- Responda de forma direta, amigável e em português brasileiro.
- Use os dados acima para responder perguntas específicas sobre finanças.
- Se não souber algo, diga claramente.
- Use emojis com moderação para tornar a resposta mais agradável.
- Seja concisa: prefira respostas de 2–4 parágrafos curtos.
- Nunca invente dados que não foram fornecidos acima.
"""

        # 1. Tenta responder via Gemini se configurado
        if cls.is_gemini_configurado():
            try:
                import google.generativeai as genai
                genai.configure(api_key=os.getenv("GEMINI_API_KEY", "").strip())
                modelo = genai.GenerativeModel(
                    model_name=cls.GEMINI_MODEL,
                    system_instruction=system_prompt,
                    generation_config={
                        "temperature": 0.7,
                        "max_output_tokens": 512,
                    }
                )
                resposta = modelo.generate_content(pergunta)
                texto = (resposta.text or "").strip()
                if texto:
                    return texto
            except Exception as exc:
                logger.warning("AiChatService: Falha ao consultar Gemini (%s). Tentando Ollama.", exc)

        # 2. Fallback para Ollama
        payload = {
            "model": cls.OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": pergunta}
            ],
            "stream": False,
            "options": {
                "num_predict": 512,
                "temperature": 0.7,
            }
        }

        try:
            resp = requests.post(
                f"{cls.OLLAMA_HOST}/api/chat",
                json=payload,
                timeout=cls.TIMEOUT
            )
            resp.raise_for_status()
            data = resp.json()
            content = data.get("message", {}).get("content", "")
            content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
            return content or "Não foi possível gerar uma resposta."
        except requests.exceptions.ConnectionError:
            logger.warning("AiChatService: Ollama e Gemini indisponíveis.")
            return "⚠️ O assistente financeiro está temporariamente indisponível. Para ativá-lo, configure a GEMINI_API_KEY no arquivo .env ou inicie o serviço Ollama local."
        except requests.exceptions.Timeout:
            logger.warning("AiChatService: timeout ao aguardar resposta do Ollama.")
            return "⏱️ O assistente demorou demais para responder. Tente novamente com uma pergunta mais curta ou aguarde alguns instantes."
        except Exception as e:
            logger.exception("AiChatService: erro ao consultar o modelo: %s", e)
            return "❌ Ocorreu um erro ao consultar o assistente. Tente novamente."
