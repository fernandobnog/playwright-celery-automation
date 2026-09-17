"""
Flow: Deep Content Writer (Autonomous AI Research & Multichannel Editorial Engine).
Etapa D do Pipeline Editorial de fernandonogueira.dev.br:
1. Agente Investigativo Autônomo: Formula buscas no Google via GoogleSearchScraper e realiza scraping
   profundo de páginas via AIExtractor, avaliando iterativamente o grau de compreensão do tema.
2. Dossiê Factual & Citação de Fontes: Consolida dados, estatísticas, tribunais, órgãos e URLs canônicas.
3. Orquestrador de Conteúdo, Storyteller Sênior & SEO Lead:
   - CANAL 1: Artigo para Blog WordPress (800-1200 palavras, Casos Reais, Analogy Engine, Framework e SEO).
   - CANAL 2: Artigo de Liderança LinkedIn Pulse (600-1100 palavras) + Post de Feed com gancho e emoji 👇.
   - CANAL 3: Material para Instagram (Legenda de Carrossel com tópicos + Roteiro Reels de 45-55s em Tabela).
   - CANAL 4: Prompts de Imagem em Inglês Técnico (Midjourney/FLUX/DALL-E: 1x1, 9x16, 16x9 com Paleta Institucional).
4. Entrega Multicanal: Google Docs, WhatsApp (Evolution API) e E-mail formatado (Gmail API).
"""

from datetime import datetime
import html
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urlparse
import feedparser
from pydantic import BaseModel, Field

from core.celery_app import celery_app
from core.config import settings
from integrations.evolution import EvolutionClient
from integrations.gemini import GeminiClient
from integrations.google import GoogleHub
from scrapers.ai_extractor import extractor
from scrapers.google_scraper import GoogleSearchScraper
from storage.repository import repo

logger = logging.getLogger(__name__)


# ==============================================================================
# Pydantic Schemas for Multichannel Editorial Outputs
# ==============================================================================
class BlogMetadata(BaseModel):
    veiculo: str = Field(
        default="fernandonogueira.dev.br",
        description="Veículo de publicação institucional"
    )
    slug_sugerido: str = Field(
        description="Slug amigável, sem acentos, com hifens e foco em palavras-chave (ex: ia-corporativa-e-governanca)"
    )
    meta_title: str = Field(
        description="Título SEO provocativo, focado em contraste ou curiosidade (55 a 65 caracteres)"
    )
    meta_description: str = Field(
        description="Meta description com dados concretos, dor/alerta e chamada implícita (140 a 155 caracteres)"
    )
    palavra_chave_foco: str = Field(
        default="",
        description="Termo principal com alto potencial de busca"
    )
    palavras_chave_secundarias: List[str] = Field(
        default_factory=list,
        description="3 a 4 termos semânticos correlatos"
    )
    palavras_chave: List[str] = Field(
        default_factory=list,
        description="Lista de tags estratégicas para compatibilidade retroativa"
    )
    tempo_leitura_minutos: int = Field(
        default=5,
        description="Tempo estimado de leitura em minutos"
    )

    def model_post_init(self, __context: Any) -> None:
        if not self.palavras_chave:
            tags = []
            if self.palavra_chave_foco:
                tags.append(self.palavra_chave_foco)
            tags.extend(self.palavras_chave_secundarias)
            self.palavras_chave = tags[:5] if tags else ["Tecnologia", "Inovação", "Gestão"]


class FonteCitada(BaseModel):
    titulo: str = Field(description="Título da matéria, estudo, acórdão ou decisão consultada")
    url: str = Field(description="URL da fonte na internet")
    veiculo: str = Field(description="Nome do veículo, portal ou tribunal (ex: G1, InfoQ, TJRN, CNJ, TechCrunch)")
    contribuicao: str = Field(description="Fato, estatística, caso real ou dado extraído desta fonte")


class ArtigoBlog(BaseModel):
    titulo: str = Field(description="Título H1 impactante, com quebra de paradigma ou dado surpreendente")
    subtitulo: str = Field(description="Subtítulo ou lead explicativo de abertura que situa o leitor")
    tempo_leitura_minutos: int = Field(default=5, description="Tempo estimado de leitura em minutos")
    corpo_markdown: str = Field(
        description=(
            "Artigo completo para Blog WordPress (800 a 1.200 palavras, contagem rigorosa), em Markdown rico: "
            "1. Introdução (3-4 parágrafos curtos com choque de realidade, paradoxo central e tese); "
            "2. Seções H2 de Histórias e Casos Reais (TJRN, TJPR, TRT-8, escândalos ou empresas reais, mostrando o erro e o preço pago); "
            "3. Seções H2/H3 de Conceituação Acessível aplicando a 'Fórmula da Tradução Narrativa' e analogias pop "
            "(De Volta para o Futuro, Cavalo de Troia em PDF, cão farejador, estagiário sem CPF, etc.); "
            "4. Framework Aplicável (Tabela Markdown, Níveis de Maturidade 1 a 4, Pilares Inegociáveis ou Checklist de Sobrevivência); "
            "5. Conclusão memorável centrada no elemento humano com arquétipo inspirador (ex: O Advogado Maestro, O Arquiteto Orquestrador) "
            "e frase de efeito sintética; "
            "6. Seção final '## Referências e Fontes Consultadas' com hiperlinks [Nome](URL) de todas as pesquisas reais."
        )
    )
    seo: BlogMetadata = Field(description="Metadados completos de SEO para WordPress")
    fontes_citadas: List[FonteCitada] = Field(
        default_factory=list,
        description="Lista de fontes com links válidos citadas no texto"
    )


class ArtigoLinkedIn(BaseModel):
    titulo_artigo: str = Field(
        description="Título de impacto e Liderança de Pensamento (Thought Leadership) para o Artigo do LinkedIn Pulse"
    )
    subtitulo: str = Field(
        description="Subtítulo ou lead executivo que situa o leitor corporativo sobre a relevância profissional"
    )
    tempo_leitura_minutos: int = Field(
        default=4,
        description="Tempo estimado de leitura do artigo no LinkedIn em minutos"
    )
    corpo_artigo_markdown: str = Field(
        description=(
            "Artigo completo para o LinkedIn Pulse (600 a 1100+ palavras), em formato Markdown com subtítulos H2/H3, "
            "narrativa profissional envolvente de liderança, dados e estatísticas apurados na pesquisa web, "
            "lições práticas de governança/carreira/negócios, citações de fontes e conclusão provocativa para networking"
        )
    )
    texto_post_divulgacao: str = Field(
        description=(
            "Post de Alta Performance para o Feed do LinkedIn (Companion Post): "
            "Hook magnético nas linhas 1-3 com quebra de expectativa terminando obrigatoriamente com a linha de transição e o emoji '👇'; "
            "Corpo com parágrafos curtos de 1-2 linhas, espaçamento generoso, marcadores visuais (❌, 🔹, 1️⃣), "
            "tradução do problema para o mundo dos negócios/gestão (impacto em dinheiro, reputação, compliance) "
            "e contraponto propositivo de governança; "
            "CTA com síntese inspiradora e pergunta provocativa para tomadores de decisão (gestores, sócios, diretores); "
            "6 a 8 hashtags corporativas no rodapé."
        )
    )
    gancho_inicial: str = Field(
        description="As primeiras 1 a 3 linhas do post no feed com dado surpreendente terminando com 👇"
    )
    chamada_acao: str = Field(
        description="Pergunta provocativa para gestores, sócios e diretores nos comentários"
    )
    hashtags: List[str] = Field(
        description="6 a 8 hashtags corporativas e estratégicas (ex: #DireitoDigital #InteligenciaArtificial #GovernancaCorporativa)"
    )
    fontes_mencionadas: List[str] = Field(
        default_factory=list,
        description="Veículos, relatórios ou dados citados no artigo/post"
    )
    sugestao_imagem_capa: Optional[str] = Field(
        default=None,
        description="Descrição conceitual para a imagem de capa do Artigo no LinkedIn"
    )
    texto_post: Optional[str] = Field(
        default=None,
        description="Espelho do texto de divulgação para compatibilidade retroativa"
    )

    def model_post_init(self, __context: Any) -> None:
        if not self.texto_post:
            self.texto_post = self.texto_post_divulgacao


# Alias para retrocompatibilidade
PostLinkedIn = ArtigoLinkedIn


class CenaReels(BaseModel):
    tempo: str = Field(description="Janela de tempo da cena, ex: '00-05s', '06-17s', '18-35s', '36-50s'")
    cena: str = Field(description="O que aparece na tela: ações visuais, cortes, B-rolls, textos na tela, efeitos visuais")
    audio: str = Field(description="O que você fala: fala falada, ágil e natural, ritmo de investigação tech com efeitos sonoros sugeridos")


class MaterialInstagram(BaseModel):
    legenda: str = Field(
        description=(
            "Legenda para Post / Carrossel: Primeira linha com emoji magnético em CAIXA ALTA; "
            "Resumo em tópicos dos pontos mais curiosos ou chocantes da pauta utilizando 👉, 🚨, 🔍; "
            "Conclusão com CTA clara: 'Arraste para o lado', 'Salve este post' ou 'Envie para um colega'."
        )
    )
    hashtags: List[str] = Field(
        description="Bloco de 10 a 14 hashtags equilibradas entre nicho técnico, jurídico/setorial e inovação ampla"
    )
    texto_reels: str = Field(
        description=(
            "Roteiro dinâmico para Reels/TikTok (45 a 55 segundos) formatado obrigatoriamente como Tabela Markdown "
            "com as colunas: | Tempo | O que aparece na tela (Cena) | O que você fala (Áudio) |. "
            "Estilo de edição acelerada, ritmo de 'investigação tech' e efeitos sonoros sugeridos (zoom, sirene, freio, alerta)."
        )
    )
    cenas_reels: List[CenaReels] = Field(
        default_factory=list,
        description="Lista estruturada das cenas do roteiro de Reels"
    )


class PromptsImagem(BaseModel):
    imagem_feed_instagram_1x1: str = Field(
        description=(
            "Prompt em inglês técnico para Midjourney v6/FLUX/DALL-E 3, proporção 1:1, "
            "composição quadrada focada em símbolo central forte (estátua futurista, holograma, selo de segurança), "
            "aplicando a paleta corporativa: Carmine Red (#ba2649), Deep Burgundy (#922824), Gray Taupe (#a79f97), "
            "Midnight Charcoal (#191b27), Pure Optical White (#ffffff). Estilo editorial tech contemporâneo, "
            "iluminação volumétrica, estética Pixar-meets-Cyberpunk refinada, texturas realistas."
        )
    )
    imagem_reels_9x16: str = Field(
        description=(
            "Prompt em inglês técnico, proporção 9:16 vertical dinâmica, elementos em queda, linhas de código em laser, "
            "perspectiva acelerada para telas móveis, aplicando a paleta institucional (#ba2649, #922824, #a79f97, #191b27, #ffffff)."
        )
    )
    imagem_wordpress_16x9: str = Field(
        description=(
            "Prompt em inglês técnico, proporção 16:9 panorâmica cinematográfica tipo capa de revista tech "
            "(mesa executiva, servidores futuristas, telas divididas em contraste), aplicando a paleta institucional."
        )
    )
    paleta_hex: List[str] = Field(
        default=["#ba2649", "#922824", "#a79f97", "#191b27", "#ffffff"],
        description="Códigos hexadecimais da paleta institucional utilizada"
    )


class ContentGenerationResult(BaseModel):
    tema_selecionado: str = Field(description="Título do tema selecionado")
    categoria: str = Field(description="Categoria da pauta (TI, Direito Digital, Regulação, Inovação, Música)")
    dossie_pesquisa: Optional[str] = Field(
        default=None,
        description="Síntese analítica das matérias e fatos pesquisados na internet pelo agente autônomo"
    )
    total_fontes_analisadas: int = Field(
        default=0,
        description="Total de fontes web raspadas e compreendidas pelo agente"
    )
    blog: ArtigoBlog = Field(description="CANAL 1: Artigo para Blog WordPress (800-1200 palavras) com SEO, Casos Reais e Framework")
    linkedin: ArtigoLinkedIn = Field(description="CANAL 2: Artigo LinkedIn Pulse + Post de Alta Performance para o Feed")
    instagram: MaterialInstagram = Field(description="CANAL 3: Material para Instagram (Legenda de Carrossel + Roteiro Reels 45-55s)")
    prompts_imagem: PromptsImagem = Field(description="CANAL 4: Prompts em inglês técnico para Midjourney/FLUX com paleta institucional")
    fontes_pesquisadas: List[FonteCitada] = Field(
        default_factory=list,
        description="Lista de todas as fontes pesquisadas e assimiladas pelo agente"
    )


# Modelos auxiliares para o loop de pesquisa autônoma
class SearchQueryPlan(BaseModel):
    queries: List[str] = Field(
        description="Exatamente 2 termos de busca otimizados para o Google Search para explorar o tema com profundidade factual"
    )
    intencao_investigativa: str = Field(
        description="Objetivo investigativo do que precisa ser descoberto na internet"
    )


class ComprehensionAssessment(BaseModel):
    tema_compreendido: bool = Field(
        description="true se o tema já foi profundamente compreendido com fatos, dados, números e contexto suficientes para uma redação autoritativa"
    )
    grau_compreensao_score: float = Field(
        description="Score de 0.0 a 1.0 representando o grau de profundidade e solidez das informações coletadas"
    )
    fatos_e_dados_chave: List[str] = Field(
        description="Principais fatos, estatísticas, tribunais, órgãos e argumentos identificados nas matérias raspadas"
    )
    lacunas_identificadas: List[str] = Field(
        default_factory=list,
        description="Pontos, dúvidas ou dados ainda ausentes (vazio se tema_compreendido for true)"
    )
    proxima_query: Optional[str] = Field(
        default=None,
        description="Termo de busca direcionado para preencher a lacuna caso tema_compreendido seja false"
    )


# ==============================================================================
# System Instructions & Personas
# ==============================================================================
RESEARCH_PLANNER_SYSTEM_INSTRUCTION = """
Você é um Agente Investigativo e Pesquisador Sênior para o portal editorial fernandonogueira.dev.br.
Sua função é formular termos de busca precisos no Google para encontrar matérias recentes, análises técnicas,
dados de mercado, casos reais (tribunais, órgãos públicos, decisões regulatórias e incidentes) e relatórios sobre a pauta indicada.

Diretrizes:
- Busque pesquisas, decisões reais (TJRN, TJPR, TRT-8, CNJ, OAB, etc.), estudos de caso corporativos, dados de cibersegurança ou anúncios recentes (2025/2026).
- Se a categoria envolver Música & Show Business, busque matérias sobre mercado ao vivo, arrecadação de bares, ECAD, streaming e bastidores.
- Formule exatamente 2 termos de busca limpos e eficazes, sem operadores complexos que quebrem a busca.
"""

COMPREHENSION_EVALUATOR_SYSTEM_INSTRUCTION = """
Você é o Editor Executivo responsável por avaliar a profundidade e veracidade da pesquisa sobre uma pauta para fernandonogueira.dev.br.
Analise os trechos e conteúdos raspados da internet pelo nosso agente e determine se temos dados, fatos, números, casos reais e perspectivas suficientes para redigir um pacote editorial de altíssima autoridade.

Se as fontes já fornecerem dados concretos, exemplos práticos e contexto fático sem alucinar, defina 'tema_compreendido: true'.
Se ainda faltar um caso real ou houver ambiguidade, defina 'tema_compreendido: false' e formule uma próxima query altamente direcionada.
"""

CONTENT_WRITER_SYSTEM_INSTRUCTION = """
Você é o Orquestrador de Conteúdo, Storyteller Sênior e SEO Lead de fernandonogueira.dev.br.
Sua missão é transformar temas densos de tecnologia, regulação, cibersegurança e inovação em conteúdos magnéticos, ágeis e acessíveis para qualquer público, sem perder a precisão profissional e a autoridade técnica.

Você utiliza OBRIGATORIAMENTE os dados, fatos, números e fontes extraídos da pesquisa na internet pelo nosso agente investigativo para embasar todas as peças editoriais.

---

### 1. PERSONA E IDENTIDADE EDITORIAL
* **Voz:** Um especialista visionário que traduz o "tecniquês" e o "juridiquês" em linguagem de negócios viva, humana e envolvente.
* **Tom de Voz:** 
  * **Formal na medida certa, mas nunca acadêmico/engessado:** Evite termos herméticos (como "subsunção axiológica", "epistemologia probatória" ou "múnus público"), a menos que sejam imediatamente explicados por analogias claras.
  * **Divertido, mas não infantil:** Humor fino, irônico e inteligente. Use comparações da cultura pop e do cotidiano (ex.: De Volta para o Futuro, carros voadores, trapaça da tinta invisível, cão farejador). Não faça piadas soltas ou deboche; o humor deve surgir do absurdo dos fatos reais.
  * **Rápido e Cinematográfico:** Frases curtas, ritmo verbal acelerado, uso abundante de verbos de ação e ganchos em quase todos os parágrafos.
  * **Empatia com o Leitor:** Sempre responda à pergunta inconsciente de quem lê: "Por que eu deveria me importar com isso hoje?"

---

### 2. A "FÓRMULA DA TRADUÇÃO NARRATIVA" (ANALOGY ENGINE)
Ao receber relatórios técnicos, acórdãos ou artigos acadêmicos, você NUNCA resume mecanicamente. Você decompõe o conceito e aplica a seguinte matriz de substituição de conceitos:
* Prompt Injection Indireto ➔ "Cavalo de Troia em PDF", "Texto invisível com tinta branca", "Ordem secreta para hipnotizar o robô".
* Vetorização Semântica / NLP ➔ "Cão farejador de similaridade", "Radar que compara argumentos matematicamente".
* Modelos de Linguagem / IA Generativa ➔ "O estagiário ultraveloz que não tem CPF nem OAB", "O computador que não sabe pensar sozinho".
* Supervisão Humana / Human-in-the-Loop ➔ "O Advogado Maestro", "A regra de ouro do humano no volante".
* Opacidade Algorítmica (Black Box) ➔ "A caixa-preta judicial", "A decisão do 'o computador disse que sim'".
* Untrusted Data (Proseg-IA) ➔ "O fim da era do PDF inofensivo", "Tratar petição como código suspeito".
* Mercado Musical / Show Business ➔ "O maestro do bar", "O som acústico que segura a mesa", "A matemática do streaming vs o suor do palco".

---

### 3. ARQUITETURA OBRIGATÓRIA DOS 4 CANAIS (PACOTE COMPLETO)

Para cada tema ou pauta recebida, gere SEMPRE o pacote completo nos seguintes formatos:

#### CANAL 1: Artigo para Blog WordPress (redacao_wp)
* **Extensão:** 800 a 1.200 palavras (contagem rigorosa).
* **Estrutura de Metadados (Frontmatter / SEO):**
  * Veículo: fernandonogueira.dev.br
  * Título SEO: Provocativo, instigante, focado em contraste ou curiosidade (55-65 caracteres).
  * Slug: Amigável, sem acentos, com hifens e foco em palavras-chave.
  * Meta Description: Gancho com dados concretos, dor/alerta e chamada implícita (140-155 caracteres).
  * Palavra-chave Foco: Termo principal com alto potencial de busca.
  * Palavras-chave Secundárias: 3 a 4 termos semânticos correlatos.
* **Estrutura Textual do Artigo:**
  * **Título H1:** Impactante, com quebra de paradigma ou dado surpreendente.
  * **Introdução (3 a 4 parágrafos curtos):** O choque de realidade (estatística recente ou fato impressionante apurado) + O paradoxo central + A tese do artigo.
  * **Seções H2 (Histórias e Casos Reais):** NUNCA teorize no vazio. Ilustre com episódios reais de tribunais, empresas ou escândalos recentes (TJRN, TJPR, TRT-8, casos corporativos ou da indústria da música). Mostre a trapaça/erro e o preço pago.
  * **Seções H2/H3 (Conceituação Acessível):** Explicação sem fricção de como a tecnologia funciona por trás do pano aplicando o Analogy Engine.
  * **Framework Aplicável (Tabela Markdown, Níveis ou Checklist):** Entregue valor prático estruturado em "Níveis de Maturidade (1 a 4)", "Pilares Inegociáveis" ou "Passo a Passo de Sobrevivência".
  * **Conclusão:** Fechamento memorável centrado no elemento humano. Crie um arquétipo inspirador (ex.: O Advogado Maestro, O Arquiteto Orquestrador). Termine com uma frase de efeito sintética.
  * **Citação Obrigatória de Fontes:** Use hiperlinks Markdown [Nome da Fonte](URL) ao longo do texto e finalize com a seção "## Referências e Fontes Consultadas".

#### CANAL 2: Artigo de Liderança & Post de Alta Performance para LinkedIn
* **Artigo para o LinkedIn Pulse:**
  * Artigo completo e aprofundado (600 a 1100+ palavras), em tom de Thought Leadership, estruturado com título de impacto, subtítulo executivo, tempo de leitura, subtítulos temáticos, narrativa profissional, citações de fontes e conclusão provocativa.
* **Post de Alta Performance para o Feed (Companion Post):**
  * **Estrutura Visual:** Parágrafos de uma ou duas linhas, espaçamento generoso e leitura dinâmica no mobile.
  * **Hook (Linhas 1-3):** Dado surpreendente, quebra de expectativa ou contraste dramático. Termine sempre com uma linha de transição e o emoji: 👇
  * **Corpo (Storytelling Executivo):**
    * Apresentação rápida do fato ou dos casos com marcadores visuais (❌, 🔹, 1️⃣).
    * A tradução do problema para o mundo dos negócios/gestão (o impacto em dinheiro, reputação ou risco de compliance).
    * O contraponto propositivo: a estratégia correta de governança ou liderança.
  * **CTA (Chamada Final):** Síntese inspiradora + Pergunta provocativa direcionada a tomadores de decisão (gestores, sócios, diretores).
  * **Hashtags:** 6 a 8 hashtags corporativas e estratégicas no rodapé (ex.: #DireitoDigital #InteligenciaArtificial #GovernancaCorporativa).

#### CANAL 3: Material de Apoio para Instagram
* **1. Legenda para Post / Carrossel:**
  * Primeira linha com emoji magnético em CAIXA ALTA.
  * Resumo em tópicos dos pontos mais curiosos ou chocantes da pauta (👉, 🚨, 🔍).
  * Conclusão com CTA clara: "Arraste para o lado", "Salve este post" ou "Envie para um colega".
* **2. Hashtags Estratégicas:** Bloco de 10 a 14 hashtags equilibradas entre nicho técnico, jurídico e inovação ampla.
* **3. Roteiro Dinâmico para Reels / TikTok (texto_reels):**
  * **Duração:** 45 a 55 segundos.
  * **Diretrizes de Produção:** Estilo de edição acelerada, ritmo de "investigação tech", efeitos sonoros sugeridos (zoom, sirene, freio, alerta).
  * **Tabela de Produção Obrigatória (Markdown):**
    * Coluna 1: Tempo (ex.: 00-05s, 06-17s, etc.)
    * Coluna 2: O que aparece na tela (Cena) (ações visuais, cortes, B-rolls, textos na tela)
    * Coluna 3: O que você fala (Áudio) (fala falada e natural, sem rebuscamento).

#### CANAL 4: Prompts Detalhados de Geração de Imagem
Todos os prompts em **Inglês técnico**, orientados para Midjourney v6 / FLUX / DALL-E 3, aplicando a paleta corporativa:
* **Paleta Corporativa Obrigatória:**
  * Carmine Red (Ênfase / Alerta / Energia): #ba2649
  * Deep Burgundy (Sombras / Contraste elegante): #922824
  * Gray Taupe (Elementos neutros / Bases de pedra): #a79f97
  * Midnight Charcoal (Fundo imersivo): #191b27
  * Pure Optical White (Texto / Dados / Luz de recorte): #ffffff
* **Formatos Obrigatórios:**
  1. imagem_feed_instagram_1x1: Composição quadrada focada em símbolo central forte (estátua futurista, holograma, selo de segurança).
  2. imagem_reels_9x16: Composição vertical dinâmica, elementos em queda, linhas de código em laser, perspectiva acelerada para telas móveis.
  3. imagem_wordpress_16x9: Composição panorâmica cinematográfica tipo capa de revista tech (mesa executiva, servidores futuristas, telas divididas em contraste).
* **Estilo Visual:** Mix de editorial tech contemporâneo, iluminação volumétrica, estética Pixar-meets-Cyberpunk refinada, texturas realistas (mármore fosco, acrílico translúcido, néon de alta fidelidade).

---

### 4. REGRAS INEGOCIÁVEIS E ANTI-PADRÕES
1. **NUNCA use placeholders:** Jamais entregue textos com [inserir dado], [link aqui] ou [seu nome]. Todo o material deve sair 100% pronto para publicação imediata.
2. **NUNCA seja puramente alarmista:** O perigo da tecnologia deve ser apontado, mas sempre acompanhado de um caminho de solução (governança, capacitação, auditoria, protocolos).
3. **NUNCA faça artigos puramente conceituais:** Todo artigo DEVE conter pelo menos um exemplo fático, com nomes de órgãos, tribunais ou sistemas reais (ex.: TJRN, TJPR, TRT-8, Berna, Galileu, CNJ, OAB).
4. **Preserve a temporalidade contextual:** Mantenha a coerência temporal com marcos recentes (ex.: Resolução CNJ 615/2025, iniciativas de 2026).
"""


# ==============================================================================
# Autonomous Research Agent (Google Search + AI Extractor + Comprehension Loop)
# ==============================================================================
class EditorialResearchAgent:
    """
    Autonomous AI Research Agent for the Editorial Pipeline.
    Plans targeted search queries, leverages GoogleSearchScraper with Playwright,
    extracts clean markdown via AIExtractor, and iterates until the subject is
    thoroughly comprehended before writing begins.
    """

    def __init__(
        self,
        gemini_client: Optional[GeminiClient] = None,
        google_scraper: Optional[GoogleSearchScraper] = None,
        content_extractor: Optional[Any] = None,
        max_iterations: int = 2,
        max_pages_to_scrape: int = 4,
    ):
        self.gemini = gemini_client or GeminiClient()
        self.google_scraper = google_scraper
        self.extractor = content_extractor or extractor
        self.max_iterations = max_iterations
        self.max_pages_to_scrape = max_pages_to_scrape

    def plan_initial_queries(self, tema: str, categoria: str, contexto: Optional[str] = None) -> List[str]:
        """Plans two initial high-precision Google search queries."""
        prompt = (
            f"Elabore termos de busca para pesquisar a seguinte pauta editorial:\n"
            f"📌 TEMA: {tema}\n"
            f"📂 CATEGORIA: {categoria}\n"
        )
        if contexto:
            prompt += f"📝 CONTEXTO/ÂNGULO: {contexto}\n"

        try:
            plan: SearchQueryPlan = self.gemini.generate_structured(
                prompt=prompt,
                system_instruction=RESEARCH_PLANNER_SYSTEM_INSTRUCTION,
                response_model=SearchQueryPlan,
                model_name="gemini-2.5-flash",
            )
            return [q.strip() for q in plan.queries if q.strip()][:2]
        except Exception as e:
            logger.warning("Failed planning search queries via Gemini (%s). Using fallback queries.", e)
            clean_t = re.sub(r"[^\w\s]", " ", tema).strip()
            cat_keyword = "tecnologia regulacao tribunais" if "TI" in categoria or "Tecnologia" in categoria else "musica shows mercado"
            return [clean_t, f"{clean_t} {cat_keyword}"]

    def search_google(self, query: str, num_results: int = 5) -> List[Dict[str, Any]]:
        """
        Executes Google search via GoogleSearchScraper (Playwright Chromium).
        Falls back to Google News RSS if Chromium is unavailable or encounters anti-bot challenges.
        """
        logger.info("Research Agent searching Google: '%s'", query)
        # 1. Primary: GoogleSearchScraper
        try:
            if self.google_scraper:
                res = self.google_scraper.search(query=query, num_results=num_results)
            else:
                with GoogleSearchScraper() as scraper:
                    res = scraper.search(query=query, num_results=num_results)
            organic = res.get("organic_results", [])
            if organic:
                return organic
        except Exception as e:
            logger.warning("Primary GoogleSearchScraper failed for query '%s' (%s). Trying fallback...", query, e)

        # 2. Resilient Fallback: Google News RSS
        try:
            rss_url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
            feed = feedparser.parse(rss_url)
            fallback_results = []
            for entry in feed.entries[:num_results]:
                fallback_results.append({
                    "title": entry.get("title", ""),
                    "url": entry.get("link", ""),
                    "snippet": entry.get("summary", ""),
                    "date": entry.get("published", ""),
                })
            if fallback_results:
                logger.info("Retrieved %d fallback articles from Google News RSS for '%s'", len(fallback_results), query)
                return fallback_results
        except Exception as err_rss:
            logger.warning("Fallback Google News RSS search also failed: %s", err_rss)

        return []

    def scrape_article(self, url: str) -> Optional[Dict[str, Any]]:
        """Extracts clean Markdown and metadata using AIExtractor."""
        try:
            res = self.extractor.extract(url=url, mode="auto")
            if res.get("status") == "success" and res.get("content"):
                parsed_url = urlparse(url)
                netloc = parsed_url.netloc.replace("www.", "")
                title = res.get("metadata", {}).get("title") or res.get("metadata", {}).get("og_title") or netloc
                return {
                    "url": url,
                    "title": title.strip(),
                    "veiculo": netloc,
                    "content": res.get("content", "")[:3500],
                    "date": res.get("metadata", {}).get("published_time") or "",
                }
        except Exception as e:
            logger.warning("Failed scraping candidate URL '%s': %s", url, e)
        return None

    def evaluate_comprehension(
        self,
        tema: str,
        categoria: str,
        scraped_sources: List[Dict[str, Any]],
        iteration: int,
    ) -> ComprehensionAssessment:
        """Asks Gemini to evaluate if the collected research satisfies the depth criteria."""
        if not scraped_sources:
            return ComprehensionAssessment(
                tema_compreendido=False,
                grau_compreensao_score=0.1,
                fatos_e_dados_chave=[],
                lacunas_identificadas=["Nenhuma matéria raspada ainda."],
                proxima_query=f"{tema} dados estatísticas",
            )

        sources_summary = "\n\n".join([
            f"--- FONTE {i+1}: {s['title']} ({s['veiculo']}) ---\n"
            f"URL: {s['url']}\n"
            f"CONTEÚDO:\n{s['content'][:1400]}"
            for i, s in enumerate(scraped_sources)
        ])

        prompt = (
            f"📌 PAUTA: {tema} [{categoria}]\n"
            f"🔄 ITERAÇÃO DE PESQUISA: {iteration}/{self.max_iterations}\n\n"
            f"CONTEÚDOS E MATÉRIAS COLETADAS NA INTERNET:\n\n{sources_summary}\n\n"
            "Avalie se o tema já está amplamente compreendido com fatos, tribunais/órgãos e referências reais suficientes."
        )

        try:
            assessment: ComprehensionAssessment = self.gemini.generate_structured(
                prompt=prompt,
                system_instruction=COMPREHENSION_EVALUATOR_SYSTEM_INSTRUCTION,
                response_model=ComprehensionAssessment,
                model_name="gemini-2.5-flash",
            )
            return assessment
        except Exception as e:
            logger.warning("Comprehension evaluation failed via Gemini (%s). Assuming adequate if sources exist.", e)
            return ComprehensionAssessment(
                tema_compreendido=True,
                grau_compreensao_score=0.85,
                fatos_e_dados_chave=[s["title"] for s in scraped_sources],
                lacunas_identificadas=[],
            )

    def conduct_deep_research(
        self,
        tema: str,
        categoria: str,
        contexto: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executes the autonomous loop:
        Query formulation -> Google Search -> Scraping -> Comprehension check -> Next query if needed.
        """
        logger.info("Autonomous Research Agent starting investigation for: '%s' [%s]", tema, categoria)

        seen_urls = set()
        scraped_sources: List[Dict[str, Any]] = []
        queries_executed: List[str] = []

        blocked_domains = {
            "google.com", "facebook.com", "instagram.com", "twitter.com",
            "x.com", "linkedin.com", "youtube.com", "pinterest.com", "tiktok.com"
        }

        queries = self.plan_initial_queries(tema, categoria, contexto)

        iteration = 1
        last_assessment: Optional[ComprehensionAssessment] = None

        while iteration <= self.max_iterations and len(scraped_sources) < self.max_pages_to_scrape:
            logger.info("Research Iteration %d/%d with queries: %s", iteration, self.max_iterations, queries)

            new_candidates: List[Dict[str, Any]] = []
            for q in queries:
                if q in queries_executed:
                    continue
                queries_executed.append(q)
                results = self.search_google(q, num_results=5)
                for r in results:
                    u = r.get("url", "").strip()
                    domain = urlparse(u).netloc.lower()
                    if not u or any(b in domain for b in blocked_domains):
                        continue
                    if u not in seen_urls:
                        seen_urls.add(u)
                        new_candidates.append(r)

            for cand in new_candidates:
                if len(scraped_sources) >= self.max_pages_to_scrape:
                    break
                url = cand.get("url")
                scraped = self.scrape_article(url)
                if scraped and len(scraped.get("content", "").strip()) >= 50:
                    scraped_sources.append(scraped)
                    logger.info("Digested article: '%s' (%s)", scraped["title"][:50], scraped["veiculo"])

            last_assessment = self.evaluate_comprehension(tema, categoria, scraped_sources, iteration)
            logger.info(
                "Comprehension score: %.2f | Understood: %s | Gaps: %s",
                last_assessment.grau_compreensao_score,
                last_assessment.tema_compreendido,
                last_assessment.lacunas_identificadas,
            )

            if last_assessment.tema_compreendido or iteration >= self.max_iterations:
                break

            if last_assessment.proxima_query and last_assessment.proxima_query not in queries_executed:
                queries = [last_assessment.proxima_query]
            else:
                break

            iteration += 1

        fontes_consultadas: List[FonteCitada] = []
        for s in scraped_sources:
            summary_snippet = s["content"][:220].replace("\n", " ").strip()
            fontes_consultadas.append(FonteCitada(
                titulo=s["title"],
                url=s["url"],
                veiculo=s["veiculo"],
                contribuicao=summary_snippet,
            ))

        if scraped_sources:
            dossier_text = f"PESQUISA INVESTIGATIVA REALIZADA EM {len(scraped_sources)} FONTES DA INTERNET:\n\n"
            if last_assessment and last_assessment.fatos_e_dados_chave:
                dossier_text += "FATOS E DADOS PRINCIPAIS ASSIMILADOS:\n"
                for fato in last_assessment.fatos_e_dados_chave:
                    dossier_text += f"• {fato}\n"
                dossier_text += "\n"

            for i, s in enumerate(scraped_sources):
                dossier_text += (
                    f"--- FONTE {i+1}: {s['title']} ({s['veiculo']}) ---\n"
                    f"URL: {s['url']}\n"
                    f"DATA: {s.get('date') or 'Recente'}\n"
                    f"CONTEÚDO:\n{s['content']}\n\n"
                )
        else:
            dossier_text = "Nenhuma fonte web externa adicional pôde ser raspada. Redigir com conhecimento factual de alta autoridade."

        return {
            "dossie_pesquisa": dossier_text,
            "fontes": fontes_consultadas,
            "queries_executadas": queries_executed,
            "total_analisadas": len(scraped_sources),
            "grau_compreensao": last_assessment.grau_compreensao_score if last_assessment else 0.8,
        }


# ==============================================================================
# Main Orchestration & Multi-Channel Delivery
# ==============================================================================
def generate_deep_content_and_deliver(
    pauta_titulo: str,
    categoria: str,
    target_format: str = "both",
    contexto_adicional: Optional[str] = None,
    hub: Optional[GoogleHub] = None,
    evolution: Optional[EvolutionClient] = None,
    gemini_client: Optional[GeminiClient] = None,
    research_agent: Optional[EditorialResearchAgent] = None,
    execute_research: bool = True,
) -> Dict[str, Any]:
    """
    Coordinates autonomous internet research, AI content creation across all 4 channels,
    Google Docs compilation, and WhatsApp/Email delivery.
    """
    google_hub = hub or GoogleHub()
    evo = evolution or EvolutionClient()
    gemini = gemini_client or GeminiClient()

    logger.info("Starting deep multichannel editorial workflow for: '%s' [%s]...", pauta_titulo, categoria)

    # 1. Autonomous Web Research Phase
    research_data = {
        "dossie_pesquisa": "",
        "fontes": [],
        "queries_executadas": [],
        "total_analisadas": 0,
        "grau_compreensao": 1.0,
    }

    if execute_research:
        agent = research_agent or EditorialResearchAgent(gemini_client=gemini)
        try:
            research_data = agent.conduct_deep_research(
                tema=pauta_titulo,
                categoria=categoria,
                contexto=contexto_adicional,
            )
            logger.info(
                "Web research completed: %d sources scraped and digested.",
                research_data.get("total_analisadas", 0),
            )
        except Exception as err_research:
            logger.warning("Autonomous research agent encountered an error: %s. Continuing with direct synthesis.", err_research)

    # 2. Deep Writer Phase (Gemini IA) - Multichannel Orchestrator
    sources_block = ""
    if research_data.get("fontes"):
        sources_block = "FONTES DISPONÍVEIS PARA CITAÇÃO NO CORPO DO TEXTO (OBRIGATÓRIO CITAR):\n"
        for f in research_data["fontes"]:
            sources_block += f"- [{f.titulo}]({f.url}) | Veículo: {f.veiculo}\n"
    else:
        sources_block = "Nenhuma fonte web adicional. Utilize o conhecimento factual mais recente e rigoroso."

    writer_prompt = (
        f"Gere o PACOTE EDITORIAL COMPLETO (4 CANAIS) para fernandonogueira.dev.br sobre o seguinte tema:\n\n"
        f"📌 TEMA: {pauta_titulo}\n"
        f"📂 CATEGORIA: {categoria}\n"
        f"🎯 FORMATO ALVO: {target_format}\n\n"
        f"🌐 DOSSIÊ DE PESQUISA FACTUAL COLETADO NA INTERNET:\n"
        f"{research_data.get('dossie_pesquisa', '')}\n\n"
        f"{sources_block}\n\n"
        "DIRETRIZES OBRIGATÓRIAS DE REDAÇÃO:\n\n"
        "1. CANAL 1: ARTIGO PARA BLOG WORDPRESS:\n"
        "   - Extensão: 800 a 1.200 palavras (contagem rigorosa).\n"
        "   - Metadados de SEO completos: slug amigável, título SEO (55-65 caracteres), meta description (140-155 caracteres), palavra-chave foco e 3-4 secundárias.\n"
        "   - Estrutura: Título H1 magnético, Introdução de 3-4 parágrafos (choque de realidade + paradoxo + tese), Seções H2 de Casos Reais (órgãos, tribunais, empresas ou escândalos reais, com erro e preço pago), Seções H2/H3 com Conceituação Acessível aplicando o Analogy Engine (Cavalo de Troia em PDF, cão farejador, estagiário sem CPF, etc.), Framework Aplicável (Tabela, Níveis de Maturidade 1 a 4, ou Checklist) e Conclusão centrada no elemento humano com arquétipo inspirador (ex: O Advogado Maestro) e frase de efeito.\n"
        "   - CITE OBRIGATORIAMENTE as fontes no corpo do texto com hiperlinks Markdown [Nome da Fonte](URL) e crie no final a seção '## Referências e Fontes Consultadas'.\n\n"
        "2. CANAL 2: LINKEDIN PULSE & FEED:\n"
        "   - Artigo de Liderança LinkedIn Pulse (600 a 1100+ palavras): Título de impacto, subtítulo executivo, tempo de leitura, seções Markdown aprofundadas, citações e reflexão provocativa.\n"
        "   - Post de Alta Performance para o Feed (Companion Post): Hook nas linhas 1-3 terminando obrigatoriamente com a linha de transição e o emoji '👇', parágrafos curtos de 1-2 linhas, marcadores visuais (❌, 🔹, 1️⃣), tradução para o mundo dos negócios (dinheiro/reputação/compliance), contraponto propositivo de governança, CTA provocativo para tomadores de decisão e 6 a 8 hashtags corporativas.\n\n"
        "3. CANAL 3: MATERIAL PARA INSTAGRAM:\n"
        "   - Legenda para Post/Carrossel: Primeira linha com emoji em CAIXA ALTA, tópicos com 👉, 🚨, 🔍, e CTA clara para arrastar/salvar/compartilhar.\n"
        "   - 10 a 14 hashtags estratégicas equilibradas.\n"
        "   - Roteiro de Reels/TikTok de 45 a 55 segundos formatado obrigatoriamente como Tabela Markdown com as colunas: | Tempo | O que aparece na tela (Cena) | O que você fala (Áudio) | no ritmo acelerado de 'investigação tech'.\n\n"
        "4. CANAL 4: PROMPTS DE IMAGEM:\n"
        "   - 3 prompts em inglês técnico para Midjourney v6/FLUX (imagem_feed_instagram_1x1, imagem_reels_9x16, imagem_wordpress_16x9), aplicando a paleta corporativa: Carmine (#ba2649), Deep Burgundy (#922824), Gray Taupe (#a79f97), Midnight Charcoal (#191b27), Optical White (#ffffff), com iluminação volumétrica e estilo editorial tech contemporâneo Pixar-meets-Cyberpunk.\n\n"
        "NUNCA use placeholders. Todo o material deve sair 100% pronto para publicação imediata."
    )

    result: ContentGenerationResult = gemini.generate_structured(
        prompt=writer_prompt,
        system_instruction=CONTENT_WRITER_SYSTEM_INSTRUCTION,
        response_model=ContentGenerationResult,
        model_name="gemini-2.5-flash",
    )

    # Attach research metadata to final result
    result.dossie_pesquisa = research_data.get("dossie_pesquisa")
    result.total_fontes_analisadas = research_data.get("total_analisadas", 0)
    result.fontes_pesquisadas = research_data.get("fontes", [])

    # 3. Create formatted Google Docs document
    date_str = datetime.now().strftime("%d/%m/%Y")
    clean_title = pauta_titulo[:40].strip()
    doc_title = f"[Editorial] {categoria} - {clean_title} ({date_str})"
    created_doc = google_hub.docs.create_document(doc_title)
    doc_id = created_doc.get("documentId")
    doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"

    # Format list of researched sources for Google Doc
    sources_doc_section = ""
    if result.fontes_pesquisadas:
        sources_doc_section = (
            "----------------------------------------------------------\n"
            "CANAL 5: DOSSIÊ DE PESQUISA & FONTES WEB CONSULTADAS\n"
            "----------------------------------------------------------\n\n"
        )
        for i, fonte in enumerate(result.fontes_pesquisadas):
            sources_doc_section += (
                f"{i+1}. {fonte.titulo} ({fonte.veiculo})\n"
                f"   URL: {fonte.url}\n"
                f"   Fato Extraído: {fonte.contribuicao}\n\n"
            )

    doc_body = (
        "==========================================================\n"
        f"   PAUTA SELECIONADA: {result.tema_selecionado}\n"
        f"   CATEGORIA: {result.categoria} | DATA: {date_str}\n"
        f"   FONTES WEB ANALISADAS: {result.total_fontes_analisadas}\n"
        f"   PORTAL: fernandonogueira.dev.br\n"
        "==========================================================\n\n"
        "----------------------------------------------------------\n"
        "CANAL 1: ARTIGO COMPLETO PARA O LINKEDIN & POST DO FEED\n"
        "----------------------------------------------------------\n\n"
        f"# {result.linkedin.titulo_artigo}\n\n"
        f"*{result.linkedin.subtitulo}*\n\n"
        f"⏱️ Tempo de leitura estimado: {result.linkedin.tempo_leitura_minutos} min\n"
        f"🖼️ Sugestão de Imagem de Capa: {result.linkedin.sugestao_imagem_capa or 'Conceito visual executivo e moderno'}\n\n"
        f"{result.linkedin.corpo_artigo_markdown}\n\n"
        "--- [POST DE ALTA PERFORMANCE PARA O FEED DO LINKEDIN] ---\n\n"
        f"{result.linkedin.texto_post_divulgacao}\n\n"
        "----------------------------------------------------------\n"
        "CANAL 2: ARTIGO PARA BLOG WORDPRESS (com SEO & Framework)\n"
        "----------------------------------------------------------\n\n"
        f"# {result.blog.titulo}\n\n"
        f"*{result.blog.subtitulo}*\n\n"
        f"⏱️ Tempo de leitura estimado: {result.blog.seo.tempo_leitura_minutos} min\n\n"
        f"{result.blog.corpo_markdown}\n\n"
        "--- [METADADOS DE SEO - WORDPRESS] ---\n"
        f"• Veículo: {result.blog.seo.veiculo}\n"
        f"• Slug: {result.blog.seo.slug_sugerido}\n"
        f"• Título SEO ({len(result.blog.seo.meta_title)} car.): {result.blog.seo.meta_title}\n"
        f"• Meta Description ({len(result.blog.seo.meta_description)} car.): {result.blog.seo.meta_description}\n"
        f"• Palavra-Chave Foco: {result.blog.seo.palavra_chave_foco}\n"
        f"• Palavras-Chave Secundárias: {', '.join(result.blog.seo.palavras_chave_secundarias)}\n"
        f"• Tags: {', '.join(result.blog.seo.palavras_chave)}\n\n"
        "----------------------------------------------------------\n"
        "CANAL 3: MATERIAL DE APOIO PARA INSTAGRAM\n"
        "----------------------------------------------------------\n\n"
        "[LEGENDA PARA POST / CARROSSEL]\n"
        f"{result.instagram.legenda}\n\n"
        f"Hashtags: {' '.join(['#' + h.replace('#', '') for h in result.instagram.hashtags])}\n\n"
        "[ROTEIRO DINÂMICO PARA REELS / TIKTOK (45-55 segundos)]\n"
        f"{result.instagram.texto_reels}\n\n"
        "----------------------------------------------------------\n"
        "CANAL 4: PROMPTS DETALHADOS DE GERAÇÃO DE IMAGEM (Inglês Técnico)\n"
        "----------------------------------------------------------\n"
        f"Paleta Institucional: Carmine (#ba2649) | Burgundy (#922824) | Taupe (#a79f97) | Charcoal (#191b27) | White (#ffffff)\n\n"
        f"• Imagem Feed Instagram (1:1):\n{result.prompts_imagem.imagem_feed_instagram_1x1}\n\n"
        f"• Imagem Reels / Stories (9:16):\n{result.prompts_imagem.imagem_reels_9x16}\n\n"
        f"• Capa WordPress (16:9):\n{result.prompts_imagem.imagem_wordpress_16x9}\n\n"
        f"{sources_doc_section}"
    )
    google_hub.docs.append_text(doc_id, doc_body)
    logger.info("Google Doc created and populated: %s", doc_url)

    # Organize in Google Drive: place all editorial documents inside 'Editoriais' folder
    editorial_folder_id = None
    try:
        editorial_folder_id = google_hub.drive.get_or_create_folder("Editoriais")
        google_hub.drive.move_file(doc_id, editorial_folder_id)
        logger.info("Organized Google Doc %s into Google Drive folder 'Editoriais' (ID: %s)", doc_id, editorial_folder_id)
    except Exception as err_folder:
        logger.warning("Could not organize Google Doc into 'Editoriais' folder: %s", err_folder)

    delivery_status = {"whatsapp": "SKIPPED", "email": "SKIPPED"}

    # 4. Dispatch notification via WhatsApp (Evolution API)
    if settings.NOTIFICATION_PHONE:
        try:
            import asyncio
            wpp_message = (
                f"🚀 *Fernando, seu pacote editorial completo está pronto!*\n\n"
                f"📌 *Tema:* {result.tema_selecionado}\n"
                f"📂 *Categoria:* {result.categoria}\n"
                f"🌐 *Pesquisa Web Autônoma:* {result.total_fontes_analisadas} fontes analisadas.\n\n"
                f"📄 *Acesse o Google Doc para revisar e publicar:*\n{doc_url}\n\n"
                f"✨ *Pacote Completo Gerado (4 Canais):*\n"
                f"• 📝 *Blog WordPress:* 800-1200 palavras com casos reais, Analogy Engine, framework e SEO\n"
                f"• 💼 *LinkedIn:* Artigo Pulse (600-1100 pal.) + Post de Feed com gancho e emoji 👇\n"
                f"• 📸 *Instagram:* Legenda de Carrossel + Roteiro Reels (45-55s em tabela)\n"
                f"• 🎨 *Prompts de Imagem:* 1x1, 9x16 e 16x9 em inglês técnico com paleta institucional (#ba2649, #922824)"
            )
            asyncio.run(evo.send_text_message(settings.NOTIFICATION_PHONE, wpp_message))
            delivery_status["whatsapp"] = "SENT"
            logger.info("WhatsApp notification dispatched to %s", settings.NOTIFICATION_PHONE)
        except Exception as err_wpp:
            delivery_status["whatsapp"] = f"ERROR: {err_wpp}"
            logger.warning("Could not send WhatsApp notification: %s", err_wpp)

    # 5. Dispatch full content via Email (Gmail API)
    if settings.ADMIN_EMAIL:
        try:
            email_subject = f"🚀 Pacote Editorial Completo: {clean_title} - {date_str}"

            sources_html_items = ""
            if result.fontes_pesquisadas:
                sources_html_items = '<div style="margin-top: 16px; background-color: #f1f5f9; padding: 14px; border-radius: 6px;">'
                sources_html_items += '<strong style="font-size: 13px; color: #475569;">🌐 Fontes e Matérias Analisadas na Internet:</strong><ul style="margin: 8px 0 0 0; padding-left: 20px; font-size: 13px; color: #334155;">'
                for f in result.fontes_pesquisadas:
                    sources_html_items += f'<li><a href="{html.escape(f.url)}" target="_blank" style="color: #2563eb; text-decoration: underline;">{html.escape(f.titulo)}</a> ({html.escape(f.veiculo)})</li>'
                sources_html_items += "</ul></div>"

            html_email = f"""
            <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #0f172a; max-width: 720px; margin: 0 auto; padding: 20px;">
                <div style="background-color: #191b27; color: #ffffff; padding: 26px; border-radius: 12px; border-top: 4px solid #ba2649;">
                    <span style="background-color: rgba(186,38,73,0.3); color: #fca5a5; padding: 4px 10px; border-radius: 4px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.8px;">
                        {html.escape(result.categoria)}
                    </span>
                    <h1 style="margin: 14px 0 6px 0; font-size: 22px; line-height: 1.3;">{html.escape(result.tema_selecionado)}</h1>
                    <p style="margin: 0; color: #94a3b8; font-size: 14px;">
                        Pacote Editorial Completo de <strong>fernandonogueira.dev.br</strong> gerado com pesquisa investigativa ({result.total_fontes_analisadas} fontes).
                    </p>
                </div>

                <div style="margin: 22px 0; text-align: center;">
                    <a href="{doc_url}" style="background-color: #ba2649; color: #ffffff; padding: 14px 28px; border-radius: 8px; text-decoration: none; font-weight: bold; display: inline-block; box-shadow: 0 4px 12px rgba(186,38,73,0.35);">
                        📄 Abrir Pacote Completo no Google Docs &rarr;
                    </a>
                </div>

                {sources_html_items}

                <!-- CANAL 1: LINKEDIN -->
                <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 22px; margin: 20px 0;">
                    <div style="margin-bottom: 10px;">
                        <span style="background-color: #0077b5; color: #ffffff; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; text-transform: uppercase;">
                            CANAL 1 • LinkedIn Pulse & Feed
                        </span>
                        <span style="font-size: 12px; color: #64748b; margin-left: 8px;">⏱️ {result.linkedin.tempo_leitura_minutos} min de leitura</span>
                    </div>
                    <h2 style="font-size: 18px; margin: 8px 0 4px 0; color: #1e293b;">{html.escape(result.linkedin.titulo_artigo)}</h2>
                    <p style="color: #64748b; margin: 0 0 12px 0;"><em>{html.escape(result.linkedin.subtitulo)}</em></p>
                    <hr style="border: 0; border-top: 1px solid #cbd5e1; margin: 14px 0;" />
                    <pre style="white-space: pre-wrap; font-family: inherit; font-size: 14px; line-height: 22px; color: #334155;">{html.escape(result.linkedin.corpo_artigo_markdown)}</pre>

                    <div style="margin-top: 18px; background-color: #ffffff; border: 1px dashed #0077b5; border-radius: 8px; padding: 16px;">
                        <strong style="font-size: 12px; color: #0077b5; text-transform: uppercase;">📢 Post de Alta Performance para o Feed do LinkedIn:</strong>
                        <pre style="white-space: pre-wrap; font-family: inherit; font-size: 13px; line-height: 20px; color: #475569; margin: 8px 0 0 0;">{html.escape(result.linkedin.texto_post_divulgacao)}</pre>
                    </div>
                </div>

                <!-- CANAL 2: BLOG WORDPRESS -->
                <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 22px; margin-bottom: 20px;">
                    <div style="margin-bottom: 10px;">
                        <span style="background-color: #2563eb; color: #ffffff; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; text-transform: uppercase;">
                            CANAL 2 • Artigo Blog WordPress
                        </span>
                        <span style="font-size: 12px; color: #64748b; margin-left: 8px;">⏱️ {result.blog.seo.tempo_leitura_minutos} min | 800-1200 palavras</span>
                    </div>
                    <p style="font-size: 19px; margin: 0 0 6px 0;"><strong>{html.escape(result.blog.titulo)}</strong></p>
                    <p style="color: #64748b; margin: 0 0 14px 0;"><em>{html.escape(result.blog.subtitulo)}</em></p>
                    <hr style="border: 0; border-top: 1px solid #cbd5e1; margin: 14px 0;" />
                    <pre style="white-space: pre-wrap; font-family: inherit; font-size: 14px; line-height: 22px; color: #334155;">{html.escape(result.blog.corpo_markdown)}</pre>

                    <div style="margin-top: 18px; background-color: #ffffff; border: 1px solid #cbd5e1; border-radius: 8px; padding: 16px;">
                        <strong style="font-size: 12px; color: #1e293b; text-transform: uppercase;">🔍 Metadados de SEO (WordPress):</strong>
                        <p style="font-size: 13px; margin: 6px 0 2px 0;"><strong>Slug:</strong> <code>{html.escape(result.blog.seo.slug_sugerido)}</code></p>
                        <p style="font-size: 13px; margin: 2px 0;"><strong>Meta Title ({len(result.blog.seo.meta_title)} car.):</strong> {html.escape(result.blog.seo.meta_title)}</p>
                        <p style="font-size: 13px; margin: 2px 0;"><strong>Meta Description ({len(result.blog.seo.meta_description)} car.):</strong> {html.escape(result.blog.seo.meta_description)}</p>
                        <p style="font-size: 13px; margin: 2px 0;"><strong>Palavra-Chave Foco:</strong> {html.escape(result.blog.seo.palavra_chave_foco or 'N/A')}</p>
                    </div>
                </div>

                <!-- CANAL 3: INSTAGRAM -->
                <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 22px; margin-bottom: 20px;">
                    <div style="margin-bottom: 10px;">
                        <span style="background-color: #e1306c; color: #ffffff; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; text-transform: uppercase;">
                            CANAL 3 • Instagram (Carrossel & Reels)
                        </span>
                    </div>
                    <div style="background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; margin-bottom: 14px;">
                        <strong style="font-size: 13px; color: #e1306c;">📸 Legenda para Post / Carrossel:</strong>
                        <pre style="white-space: pre-wrap; font-family: inherit; font-size: 13px; line-height: 20px; color: #334155; margin: 8px 0 0 0;">{html.escape(result.instagram.legenda)}</pre>
                        <p style="font-size: 12px; color: #64748b; margin: 10px 0 0 0;">{' '.join(['#' + h.replace('#', '') for h in result.instagram.hashtags])}</p>
                    </div>

                    <div style="background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px;">
                        <strong style="font-size: 13px; color: #e1306c;">🎬 Roteiro Dinâmico para Reels (45 a 55 segundos):</strong>
                        <pre style="white-space: pre-wrap; font-family: inherit; font-size: 13px; line-height: 20px; color: #334155; margin: 8px 0 0 0;">{html.escape(result.instagram.texto_reels)}</pre>
                    </div>
                </div>

                <!-- CANAL 4: PROMPTS DE IMAGEM -->
                <div style="background-color: #191b27; color: #ffffff; border-radius: 10px; padding: 22px; margin-bottom: 20px;">
                    <div style="margin-bottom: 12px;">
                        <span style="background-color: #ba2649; color: #ffffff; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; text-transform: uppercase;">
                            CANAL 4 • Prompts IA (Midjourney / FLUX)
                        </span>
                        <span style="font-size: 12px; color: #94a3b8; margin-left: 8px;">Paleta Institucional Oficial</span>
                    </div>

                    <div style="display: flex; gap: 8px; margin: 12px 0 16px 0;">
                        <div style="background-color: #ba2649; width: 24px; height: 24px; border-radius: 4px;" title="Carmine #ba2649"></div>
                        <div style="background-color: #922824; width: 24px; height: 24px; border-radius: 4px;" title="Burgundy #922824"></div>
                        <div style="background-color: #a79f97; width: 24px; height: 24px; border-radius: 4px;" title="Taupe #a79f97"></div>
                        <div style="background-color: #191b27; border: 1px solid #475569; width: 24px; height: 24px; border-radius: 4px;" title="Charcoal #191b27"></div>
                        <div style="background-color: #ffffff; width: 24px; height: 24px; border-radius: 4px;" title="White #ffffff"></div>
                    </div>

                    <p style="font-size: 12px; color: #cbd5e1; margin: 8px 0 2px 0;"><strong>• Feed Instagram (1:1):</strong></p>
                    <code style="display: block; background-color: #0f172a; padding: 10px; border-radius: 6px; font-size: 12px; color: #fca5a5; line-height: 1.5; white-space: pre-wrap;">{html.escape(result.prompts_imagem.imagem_feed_instagram_1x1)}</code>

                    <p style="font-size: 12px; color: #cbd5e1; margin: 12px 0 2px 0;"><strong>• Reels / Stories (9:16):</strong></p>
                    <code style="display: block; background-color: #0f172a; padding: 10px; border-radius: 6px; font-size: 12px; color: #fca5a5; line-height: 1.5; white-space: pre-wrap;">{html.escape(result.prompts_imagem.imagem_reels_9x16)}</code>

                    <p style="font-size: 12px; color: #cbd5e1; margin: 12px 0 2px 0;"><strong>• Capa WordPress (16:9):</strong></p>
                    <code style="display: block; background-color: #0f172a; padding: 10px; border-radius: 6px; font-size: 12px; color: #fca5a5; line-height: 1.5; white-space: pre-wrap;">{html.escape(result.prompts_imagem.imagem_wordpress_16x9)}</code>
                </div>
            </div>
            """
            google_hub.gmail.send_email(
                to_email=settings.ADMIN_EMAIL,
                subject=email_subject,
                html_body=html_email,
            )
            delivery_status["email"] = "SENT"
            logger.info("Delivery email dispatched to %s", settings.ADMIN_EMAIL)
        except Exception as err_mail:
            delivery_status["email"] = f"ERROR: {err_mail}"
            logger.warning("Could not send delivery email: %s", err_mail)

    return {
        "status": "SUCCESS",
        "doc_id": doc_id,
        "doc_url": doc_url,
        "folder_id": editorial_folder_id,
        "folder_name": "Editoriais",
        "tema": result.tema_selecionado,
        "categoria": result.categoria,
        "total_fontes_analisadas": result.total_fontes_analisadas,
        "fontes_consultadas": [f.model_dump() for f in result.fontes_pesquisadas],
        "delivery": delivery_status,
        "blog": result.blog.model_dump(),
        "linkedin": result.linkedin.model_dump(),
        "instagram": result.instagram.model_dump(),
        "prompts_imagem": result.prompts_imagem.model_dump(),
    }


@celery_app.task(name="flows.flow_content_deep_writer.task_deep_content_generation")
def task_deep_content_generation(token_payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Celery task triggered by the email button callback or manual dispatch.
    Runs autonomous Google search & scraping research, writes 4 channels, and delivers.
    """
    task_id = "deep_content_" + datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    repo.log_flow_start(task_id, "deep_content_generation", token_payload)

    pauta_titulo = token_payload.get("pauta_titulo") or "Tema Selecionado"
    categoria = token_payload.get("categoria") or "Tecnologia da Informação (TI)"
    target_format = token_payload.get("target_format") or "both"
    contexto = token_payload.get("angulo_editorial") or token_payload.get("sintese_fiel_das_materias")

    result = generate_deep_content_and_deliver(
        pauta_titulo=pauta_titulo,
        categoria=categoria,
        target_format=target_format,
        contexto_adicional=contexto,
    )

    repo.log_flow_complete(task_id, result)
    return result
