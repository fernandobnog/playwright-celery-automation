"""
Script to generate and upload the complete end-to-end manual of the Editorial Pipeline to Google Drive.
"""

from integrations.google import GoogleHub
import json

def upload_manual():
    hub = GoogleHub()
    title = "📘 Manual Completo: Pipeline Editorial Interativo (E-mail ➔ Blog & LinkedIn)"
    doc = hub.docs.create_document(title)
    doc_id = doc.get("documentId")
    doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"

    manual_text = """================================================================================
           MANUAL OPERACIONAL & ARQUITETURA DE PONTA A PONTA
         PIPELINE EDITORIAL INTERATIVO (E-MAIL ➔ BLOG & LINKEDIN)
================================================================================

Data de Criação: 17 de Setembro de 2026
Autor: Antigravity AI Engine & Fernando Nogueira
Status: Em Produção no Omni-Flow VPS

--------------------------------------------------------------------------------
1. RESUMO EXECUTIVO: COMO TUDO FUNCIONA
--------------------------------------------------------------------------------
O objetivo desta esteira é transformar o consumo passivo de notícias em uma máquina ativa de produção de conteúdo profissional para o seu Blog e para o seu LinkedIn, mantendo você (o humano) sempre no controle de decisão editorial (filosofia Human-in-the-Loop).

O ciclo completo funciona assim:
1. Às 07:00 da manhã (ou sob demanda via API), o sistema coleta centenas de notícias recentes de fontes confiáveis no Google News RSS, divididas rigorosamente em 2 trilhas:
   - Tecnologia da Informação (TI, IA, Engenharia de Software, Cloud, Cibersegurança)
   - Música & Mercado Musical (Show Business, Música ao Vivo, Bares, Streaming, Direitos Autorais)
2. A inteligência artificial (Gemini) analisa os acontecimentos, cruza dados e formula exatamente 4 pautas estratégicas (2 de TI e 2 de Música) com títulos jornalísticos, ângulos analíticos, síntese factual e fontes.
3. Você recebe um e-mail com layout moderno escuro contendo os 4 temas. Cada tema possui um botão exclusivo:
   [ 🚀 Desenvolver Artigo & LinkedIn → ]
4. Quando você gosta de um tema, basta dar 1 clique no botão (pelo celular ou pelo computador).
5. Seu navegador abre imediatamente uma página limpa confirmando: "Tema Selecionado com Sucesso! Agente redator em execução...".
6. Em segundo plano (~34 segundos), os Workers assíncronos do Celery acionam o Gemini 2.5 Flash / Pro para aprofundar o assunto e redigir dois formatos completos:
   - Um Artigo Completo para Blog em Markdown com H2/H3 e Metadados de SEO.
   - Um Post de Alta Performance para LinkedIn com gancho, ritmo dinâmico, bullets e chamada para discussão (CTA).
7. O sistema entrega o resultado automaticamente em três canais:
   - Cria um novo Google Doc no seu Google Drive com tudo formatado e pronto para você editar/publicar.
   - Envia uma notificação no seu WhatsApp com o link direto do Google Doc.
   - Envia um e-mail de entrega com o conteúdo na íntegra para cópia rápida.

--------------------------------------------------------------------------------
2. DETALHAMENTO DE CADA ETAPA DO PIPELINE
--------------------------------------------------------------------------------

ETAPA A: A CURADORIA MATINAL (07:00 BRT)
- Arquivo Principal: flows/flow_editorial_pautas.py
- Agendamento: Celery Beat (beat_schedule -> 'daily-editorial-pautas-07am')
- Coleta de Dados:
  O sistema consulta dois conjuntos de RSS Feeds no Google News em tempo real, filtrando apenas artigos publicados nos últimos 6 dias e eliminando duplicatas de múltiplos veículos.
- Análise com Gemini IA:
  O modelo Gemini analisa as matérias e gera uma estrutura tipada (Pydantic) garantindo equilíbrio estrito: 2 pautas na categoria 'Tecnologia da Informação (TI)' e 2 pautas na categoria 'Música & Mercado Musical'.

ETAPA B: O E-MAIL INTERATIVO & TOKENS DE SEGURANÇA (HMAC-SHA256)
- Template: templates/pautas_email.html
- Módulo de Segurança: core/security.py
- Como os botões funcionam:
  Para que você não precise fazer login ou autenticar em telas complexas no celular, o sistema gera um token assinado digitalmente com HMAC-SHA256 (função create_editorial_action_token).
  O token embute:
  • ID numérico da pauta (1 a 4)
  • Título exato da pauta
  • Categoria (TI ou Música)
  • Data de expiração (válido por 48 horas)
  • Assinatura criptográfica inviolável usando o segredo interno do seu servidor.
- URL do Botão:
  https://www.fernandonogueira.dev.br/api/v1/editorial/select?token=[TOKEN_ASSINADO]

ETAPA C: O ENDPOINT DE CALLBACK NO BACKEND & PROXY REVERSO
- Arquivo da Rota: api/routes/editorial.py
- Configuração de Borda: /root/composes/site/nginx.conf
- Fluxo de Rede:
  1. Seu clique bate no Nginx do site (www.fernandonogueira.dev.br).
  2. O Nginx identifica a rota '/api/v1/editorial/' e faz o proxy reverso interno na rede Docker para o gateway FastAPI (omniflow_api:8000).
  3. O endpoint valida a assinatura do token:
     - Se o token foi adulterado ou passou de 48 horas, exibe uma tela amigável avisando da expiração.
     - Se o token é autêntico, dispara a tarefa task_deep_content_generation na fila 'flows' do Celery e retorna imediatamente a página HTML de sucesso para o seu navegador (sem travar sua navegação).

ETAPA D: O AGENTE REDATOR AUTÔNOMO (GEMINI IA DEEP WRITER)
- Arquivo Principal: flows/flow_content_deep_writer.py
- Tarefa Celery: task_deep_content_generation
- Engenharia de Prompts & Personas:
  O Gemini é acionado com diretrizes rígidas de redação profissional:
  
  1. FORMATO ARTIGO DE BLOG:
     • Estrutura: Título H1 magnético, subtítulo explicativo, tempo estimado de leitura, seções H2 e H3 (contextualização, análise crítica, lições práticas e conclusão reflexiva).
     • Caixa de SEO Completa:
       - Slug amigável para URL (ex: ia-corporativa-e-governanca)
       - Meta Title (50 a 60 caracteres)
       - Meta Description (140 a 160 caracteres prontos para o Google)
       - Palavras-chave estratégicas (tags)
     • Adaptação de Tom: Didático e técnico para TI; apaixonado e com visão de mercado para Música.

  2. FORMATO POST PARA LINKEDIN:
     • O Gancho (Hook): As 2 primeiras linhas são estrategicamente redigidas para quebrar a rolagem do feed e instigar o clique no "...ver mais".
     • Escaneabilidade: Parágrafos curtos de 1 a 2 linhas com espaçamento visual para leitura rápida no celular.
     • Núcleo de Conteúdo: Bullet points com aprendizados acionáveis.
     • Chamada para Ação (CTA): Pergunta aberta no final para incentivar comentários (gatilho de alcance do algoritmo).
     • Hashtags estratégicas: 3 a 5 tags direcionadas ao nicho.

ETAPA E: ENTREGA MULTICANAL E GESTÃO
- Módulo Google: integrations/google/docs.py e integrations/google/gmail.py
- Módulo WhatsApp: integrations/evolution.py
- Canais de Recebimento:
  1. Google Drive / Docs:
     O sistema cria automaticamente um arquivo Google Docs na sua conta com o título:
     '[Rascunho] {Categoria} - {Título} ({Data})'
     O documento vem organizado com seções claras: o post do LinkedIn no topo, o artigo do blog formatado logo abaixo e os metadados de SEO ao final.
  2. WhatsApp:
     Uma mensagem é enviada para o seu WhatsApp pessoal (5519998256557) via Evolution API com o link direto do documento recém-criado:
     "🚀 Fernando, seu conteúdo está pronto! Acesse o Google Doc para revisar e publicar: [LINK]"
  3. E-mail:
     Um e-mail é enviado para fernando.bnog@gmail.com com a prévia dos dois textos no corpo do e-mail.

--------------------------------------------------------------------------------
3. COMANDOS ÚTEIS E DISPAROS MANUAIS
--------------------------------------------------------------------------------

Caso você não queira esperar as 07:00 da manhã e deseje acionar o fluxo imediatamente para receber novas pautas no seu e-mail:

Opção 1: Via Endpoint REST (cURL no terminal ou navegador)
curl -X POST http://127.0.0.1:8000/api/v1/flows/editorial-pautas \\
  -H "X-API-Key: omniflow_232750db9cac2682c20ffadd0bce268f2d85764bc1149921"

Opção 2: Via Comando Docker direto no Worker
docker exec omniflow_worker_1 python3 -c "
from flows.flow_editorial_pautas import task_daily_editorial_curation
task_daily_editorial_curation()
"

Para monitorar as tarefas sendo processadas em tempo real:
- Painel Flower (Web): http://localhost:5555 (ou via proxy NPM)
- Logs dos Workers:
  docker logs -f omniflow_worker_1
  docker logs -f omniflow_worker_2

--------------------------------------------------------------------------------
4. ARQUITETURA TÉCNICA E SEGURANÇA DOS DADOS
--------------------------------------------------------------------------------
- Zero-Trust: O serviço opera com chaves de API internas e isolamento estrito de rede Docker (proxy-network).
- Resiliência de IA: O cliente Gemini possui fallback automático de modelos (tenta gemini-2.5-flash, gemini-flash-latest e gemini-2.5-flash-lite) com retries exponenciais contra instabilidades temporárias de 503/429.
- Autonomia e Baixo Custo: Roda 100% no seu servidor VPS existente, sem servidores adicionais, utilizando os containers Docker já otimizados.
- Testes Automatizados: O sistema conta com 79 testes automatizados (100% de sucesso) cobrindo desde a geração de tokens até a formatação do HTML e criação de documentos.

================================================================================
                  FIM DO MANUAL OPERACIONAL - OMNI-FLOW
================================================================================
"""

    hub.docs.append_text(doc_id, manual_text)
    print(f"DOCUMENT_URL: {doc_url}")
    print(f"DOCUMENT_ID: {doc_id}")
    return doc_url

if __name__ == "__main__":
    upload_manual()
