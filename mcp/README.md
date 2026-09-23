# OmniFlow Lead Enrichment - MCP Server (Model Context Protocol)

Este servidor implementa a especificação oficial do **Model Context Protocol (MCP)** (Versão `2024-11-05`), permitindo que assistentes de IA como **Claude Desktop**, **Cursor**, **Windsurf**, **Cline** e **Antigravity** executem diretamente tarefas de inteligência comercial e enriquecimento de empresas em linguagem natural.

---

## 🛠️ Ferramentas Disponíveis no MCP

| Ferramenta | Descrição | Parâmetros |
| :--- | :--- | :--- |
| `enrich_company` | Enriquecimento 360° unificado: Google Search (CNPJ, Razão Social, Sede, Site) + Perfil da Empresa no LinkedIn + Decisores (C-Level, Diretores) + Inteligência de Vendas e Pitch. | `name` (obrigatório), `location` (opcional), `deep_scrape` (opcional, default: true) |
| `find_decision_makers` | Localiza e audita perfis de tomadores de decisão (Sócios, CEO, CTO, Diretores, Heads) no LinkedIn com checagem de vínculo atual. | `company_name` (obrigatório), `website_or_domain` (opcional), `max_results` (opcional, default: 10) |
| `get_linkedin_company_profile` | Extrai exclusivamente a página institucional da empresa no LinkedIn (Company Page): slogan, porte/faixa de funcionários, setor oficial, especialidades e link de vagas abertas. | `name` (obrigatório), `location` (opcional) |

---

## ⚙️ Como Configurar nos Clientes de IA

### 1. Claude Desktop

No arquivo de configuração do Claude Desktop:
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux:** `~/.config/Claude/claude_desktop_config.json`

Adicione a seção `mcpServers`:

```json
{
  "mcpServers": {
    "omniflow-lead-enrichment": {
      "command": "python3",
      "args": [
        "/root/playwright-celery-automation/mcp/server.py"
      ],
      "env": {
        "OMNIFLOW_API_URL": "https://api.fernandonogueira.dev.br",
        "OMNIFLOW_API_KEY": "omniflow_232750db9cac2682c20ffadd0bce268f2d85764bc1149921"
      }
    }
  }
}
```

> **Se você estiver rodando o Claude Desktop na sua máquina local:**  
> Você pode baixar apenas o arquivo `server.py` para a sua máquina e apontar o `command` para o seu interpretador Python local. Como ele usa apenas a biblioteca padrão do Python (`json`, `urllib`), você **não precisa instalar nenhuma dependência** via pip!

---

### 2. Cursor IDE

No Cursor, acesse **Settings > Features > MCP Servers > Add New MCP Server** ou adicione em `.cursor/mcp.json` na raiz do seu projeto:

```json
{
  "mcpServers": {
    "lead-enrichment": {
      "command": "python3",
      "args": ["/root/playwright-celery-automation/mcp/server.py"],
      "env": {
        "OMNIFLOW_API_URL": "https://api.fernandonogueira.dev.br",
        "OMNIFLOW_API_KEY": "omniflow_232750db9cac2682c20ffadd0bce268f2d85764bc1149921"
      }
    }
  }
}
```

---

### 3. Execução via Docker (Sem Python no Host)

Se preferir rodar através do container Docker:

```json
{
  "mcpServers": {
    "omniflow-enrichment-docker": {
      "command": "docker",
      "args": [
        "exec",
        "-i",
        "-e", "OMNIFLOW_API_URL=http://127.0.0.1:8000",
        "-e", "OMNIFLOW_API_KEY=omniflow_232750db9cac2682c20ffadd0bce268f2d85764bc1149921",
        "omniflow_api",
        "python3",
        "/app/mcp/server.py"
      ]
    }
  }
}
```

---

## 💬 Exemplos de Prompts para Usar com a IA

Uma vez conectado, basta conversar normalmente com o Claude, Cursor ou outro agente:

* *"Enriqueça a empresa Matera e me traga o CNPJ, o que ela faz e a lista dos principais executivos no LinkedIn."*
* *"Quem é o CTO ou diretor de tecnologia da Totvs no LinkedIn e qual o melhor gancho comercial para abordá-lo?"*
* *"Busque os dados corporativos da empresa Kavak no LinkedIn: quantos funcionários tem e quais vagas estão abertas?"*
