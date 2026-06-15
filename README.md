# 🍯 Doce como Mel

Sistema web de **controle de gastos para confeitaria**, desenvolvido como Projeto de Extensão. Permite o registro, categorização e acompanhamento de despesas, com geração de relatórios em PDF.

---

## 🚀 Tecnologias

| Camada | Tecnologia |
|---|---|
| Backend | Python 3 + Flask |
| ORM | Flask-SQLAlchemy |
| Banco de Dados | PostgreSQL (psycopg2) |
| Relatórios | ReportLab |
| Templates | Jinja2 (HTML/CSS) |
| Variáveis de Ambiente | python-dotenv |

---

## 📁 Estrutura do Projeto

```
Projeto_Extensao/
├── app/                  # Pacote principal da aplicação Flask
│   ├── models/           # Modelos do banco de dados (SQLAlchemy)
│   │   └── categoria.py  # Modelo de categorias de despesas
│   └── ...               # Rotas, templates, etc.
├── db/                   # Scripts e configurações de banco de dados
├── run.py                # Ponto de entrada da aplicação
├── requirements.txt      # Dependências do projeto
└── .env                  # Variáveis de ambiente (não versionado)
```

---

## ⚙️ Instalação

### Pré-requisitos

- Python 3.10+
- PostgreSQL instalado e rodando
- `pip` atualizado

### Passo a passo

```bash
# 1. Clone o repositório
git clone https://github.com/SimoesDev1/Projeto_Extensao.git
cd Projeto_Extensao

# 2. Crie e ative um ambiente virtual
python -m venv venv
source venv/bin/activate      # Linux/macOS
venv\Scripts\activate         # Windows

# 3. Instale as dependências
pip install -r requirements.txt

# 4. Configure as variáveis de ambiente
cp .env.example .env
# Edite o .env com as credenciais do seu banco PostgreSQL
```

### Configuração do `.env`

```env
DATABASE_URL=postgresql://usuario:senha@localhost:5432/seu_banco
SECRET_KEY=sua_chave_secreta_aqui
```

---

## ▶️ Executando

```bash
python run.py
```

A aplicação estará disponível em `http://127.0.0.1:5000`.

> Na primeira execução, categorias padrão são inseridas automaticamente no banco:
> `Ingredientes`, `Embalagens`, `Transporte`, `Contas Fixas`, `Equipamentos`, `Marketing`, `Outros`.

---

## 📦 Dependências

```
Flask==3.0.3
Flask-SQLAlchemy==3.1.1
Werkzeug==3.0.3
reportlab==4.5.0
python-dotenv==1.0.1
psycopg2-binary>=2.9.9
```

---

## 🗂️ Funcionalidades

- ✅ Cadastro e listagem de despesas
- ✅ Categorização de gastos (ingredientes, embalagens, transporte, etc.)
- ✅ Geração de relatórios em PDF com ReportLab
- ✅ Exportação de dados em CSV
- ✅ Interface web responsiva com HTML/CSS + Jinja2

---

## 🤝 Contribuição

1. Faça um fork do projeto
2. Crie uma branch para sua feature: `git checkout -b feature/nova-funcionalidade`
3. Commit suas alterações: `git commit -m 'feat: adiciona nova funcionalidade'`
4. Push para a branch: `git push origin feature/nova-funcionalidade`
5. Abra um Pull Request

---

## 📄 Licença

Este projeto foi desenvolvido para fins acadêmicos como Projeto de Extensão no **Instituto Infnet**.

---

<p align="center">Desenvolvido por <a href="https://github.com/SimoesDev1">SimoesDev1</a></p>
