# TraderIA Spec

## Objetivo

Criar um agente de paper trading para acoes dos EUA que use dados reais de mercado, noticias reais, timing, sentimento e feedback historico para decidir entre `BUY`, `SELL` e `HOLD`.

O projeto nao deve operar dinheiro real nesta fase.

## Regras De Seguranca

- Nao usar ordens reais.
- Nao salvar secrets no codigo.
- Nao colar chaves em arquivos versionados.
- Nao usar OpenAI API paga automaticamente.
- Usar paper trade local em SQLite enquanto nao houver broker paper externo confiavel.
- Com dados reais de mercado, nunca usar noticias sinteticas.
- Resultados de performance devem usar `--market-provider yahoo` e `--news-provider yahoo`.
- Dados sinteticos servem apenas para testes offline/smoke tecnico; nao usar metricas sinteticas como evidencia de estrategia.

## Estado Atual

### Market Data

- `synthetic`: dados sinteticos para testes offline e suite automatizada.
- `yahoo`: dados historicos reais sem chave via Yahoo Finance chart endpoint.

Provider recomendado agora:

```powershell
--market-provider yahoo
```

### Noticias

- `synthetic`: apenas para mercado sintetico.
- `yahoo`: noticias reais via Yahoo Finance search.
- `none` / `neutral`: sem noticias, sentimento neutro.
- `auto`: usa sinteticas apenas com mercado sintetico; usa Yahoo com mercado real.

Provider recomendado agora:

```powershell
--news-provider yahoo
```

### Sentimento

- `lexicon`: local, gratuito, sem API paga.
- `hybrid`: lexico + LLM, mas so deve ser usado se o usuario aceitar custo de API.
- `openai` / `codex` / `llm`: caminho via OpenAI Responses API, nao usar por padrao.

Provider recomendado agora:

```powershell
--sentiment-provider lexicon
```

### Broker

- `PaperBroker`: simula execucao local.
- Salva ordens, decisoes, snapshots e feedback em SQLite.

## Comandos Principais

Teste com dados reais, noticias reais e paper trade local:

```powershell
python -m traderia.cli --db data\yahoo_real_news_paper.sqlite3 simulate --market-provider yahoo --news-provider yahoo --sentiment-provider lexicon --symbols AAPL MSFT NVDA --days 90 --reset-db
```

Teste com controles de saida e benchmark:

```powershell
python -m traderia.cli --db data\yahoo_structural_paper.sqlite3 simulate --market-provider yahoo --news-provider yahoo --sentiment-provider lexicon --symbols AAPL MSFT NVDA --days 90 --min-confidence 0.55 --stop-loss-pct 0.03 --take-profit-pct 0.05 --trailing-stop-pct 0.04 --reset-db
```

Teste com overlay de regime SPY/QQQ e winners mais livres:

```powershell
python -m traderia.cli --db data\yahoo_regime_overlay.sqlite3 simulate --market-provider yahoo --news-provider yahoo --sentiment-provider lexicon --symbols AAPL MSFT NVDA --days 90 --min-confidence 0.50 --stop-loss-pct 0.03 --take-profit-pct 0 --trailing-stop-pct 0.10 --min-market-regime -0.10 --reset-db
```

Experimento opcional de saida por perda de momentum:

```powershell
python -m traderia.cli --db data\yahoo_momentum_exit_experiment.sqlite3 simulate --market-provider yahoo --news-provider yahoo --sentiment-provider lexicon --symbols AAPL MSFT NVDA --days 90 --momentum-exit-threshold -0.005 --reset-db
```

Modo overlay em benchmark:

```powershell
python -m traderia.cli --db data\yahoo_overlay_1y.sqlite3 simulate --mode overlay --market-provider yahoo --news-provider yahoo --overlay-symbol SPY --benchmark-symbols SPY QQQ --days 251 --reset-db
```

Modo growth overlay:

```powershell
python -m traderia.cli --db data\growth_overlay_1y.sqlite3 simulate --mode growth-overlay --market-provider yahoo --news-provider yahoo --growth-symbols SPY QQQ --benchmark-symbols SPY QQQ --days 251 --reset-db
```

Grid de pesquisa do overlay:

```powershell
python C:\Users\pedro\.codex\skills\traderia-research\scripts\run_overlay_grid.py --repo C:\Users\pedro\Desktop\Code\TraderIA --days 251 --market-provider yahoo --news-provider yahoo
```

Relatorio:

```powershell
python -m traderia.cli --db data\yahoo_real_news_paper.sqlite3 report
```

Explicabilidade por decisao:

```powershell
python -m traderia.cli --db data\yahoo_real_news_paper.sqlite3 explain --limit 20
python -m traderia.cli --db data\yahoo_real_news_paper.sqlite3 explain --action BUY --limit 5
```

Testes:

```powershell
python -m unittest discover -s tests
```

## Ultima Rodada Real

Periodo: `2026-02-02` ate `2026-05-06`.

Configuracao:

```text
market-provider: yahoo
news-provider: yahoo
sentiment-provider: lexicon
symbols: AAPL MSFT NVDA
days: 90
starting cash: 100000
```

Resultado:

```text
Dinheiro inicial:   $100,000.00
Dinheiro final:     $100,604.37
Resultado:          +$604.37
Eficiencia:         +0.60%
Max drawdown:       -1.80%
Trades fechados:    0
```

Operacoes:

```text
NVDA BUY 74 @ $196.51
MSFT BUY 25 @ $422.79
```

Conclusao:

- A rodada ficou positiva, mas ainda sem trades fechados.
- O resultado melhorou quando removemos noticias sinteticas dos dados reais.
- Antes, a compra ruim de AAPL foi causada por sentimento sintetico contaminando preco real.

## Rodada Estrutural Com Saidas E Benchmark

Periodo: aproximadamente 90 dias ate `2026-05-06`.

Configuracao:

```text
market-provider: yahoo
news-provider: yahoo
sentiment-provider: lexicon
symbols: AAPL MSFT NVDA
benchmarks: SPY QQQ
min confidence: 0.55
stop loss: 3%
take profit: 5%
trailing stop: 4%
starting cash: 100000
```

Resultado:

```text
Dinheiro inicial:   $100,000.00
Dinheiro final:     $100,525.66
Resultado:          +$525.66
Eficiencia:         +0.53%
Max drawdown:       -0.58%
Trades fechados:    1
Win rate:           100.00%
SPY:                +6.30%
QQQ:                +11.52%
```

Operacoes:

```text
NVDA BUY 59 @ $196.51
MSFT BUY 24 @ $420.26
NVDA SELL 59 @ $208.27 take_profit
```

Conclusao:

- A estrategia agora realiza saida e produz PnL fechado.
- O drawdown caiu, mas o bot ainda perdeu para SPY e QQQ nessa janela.
- O sistema continua mais proximo de swing conservador com overlay de risco do que de trader ativo.

## Rodada Com Regime De Mercado

Periodo: aproximadamente 90 dias ate `2026-05-06`.

Configuracao:

```text
market-provider: yahoo
news-provider: yahoo
sentiment-provider: lexicon
symbols: AAPL MSFT NVDA
benchmarks/regime: SPY QQQ
min confidence: 0.50
stop loss: 3%
take profit: desativado
trailing stop: 10%
min market regime: -0.10
starting cash: 100000
```

Resultado:

```text
Dinheiro inicial:   $100,000.00
Dinheiro final:     $100,488.11
Resultado:          +$488.11
Eficiencia:         +0.49%
Max drawdown:       -1.48%
Trades fechados:    0
SPY:                +6.30%
QQQ:                +11.52%
```

Operacoes:

```text
NVDA BUY 58 @ $196.51
MSFT BUY 25 @ $420.26
```

Conclusao:

- O regime SPY/QQQ entrou no score e na decisao de abertura.
- Remover take profit deixou os winners correrem, mas voltou a gerar posicoes abertas sem PnL fechado na janela.
- Um experimento com `--momentum-exit-threshold -0.005` fechou 2 trades, mas piorou o resultado para `-0.13%`; por isso a saida por momentum ficou opt-in.

## Rodada Overlay SPY 1 Ano

Periodo: aproximadamente 251 pregoes ate `2026-05-06`.

Configuracao:

```text
mode: overlay
market-provider: yahoo
news-provider: yahoo
overlay-symbol: SPY
benchmarks/regime: SPY QQQ
starting cash: 100000
```

Resultado:

```text
Dinheiro inicial:   $100,000.00
Dinheiro final:     $105,823.91
Resultado:          +$5,823.91
Eficiencia:         +5.82%
Max drawdown:       -5.02%
Sharpe:             0.98
Sortino:            0.96
Trades fechados:    22
SPY:                +30.77%, DD -9.13%, Sharpe 2.23
QQQ:                +43.96%, DD -12.19%, Sharpe 2.34
```

Conclusao:

- O overlay reduziu drawdown contra SPY, mas sacrificou retorno e Sharpe demais.
- Nesta janela bull market, ficar subexposto foi caro.
- A hipotese de produto continua plausivel como defesa em mercado ruim, mas precisa ser validada em janelas com quedas relevantes.
- O proximo teste deve comparar 2 anos e 5 anos, e tambem uma grade de exposicoes menos conservadora.

## Grid Overlay Continuo Com Sentimento

Periodo: aproximadamente 251 pregoes ate `2026-05-06`.

Configuracao:

```text
mode: overlay
market-provider: yahoo
news-provider: yahoo
overlay-symbol: SPY
benchmarks/regime: SPY QQQ
sentiment-provider: lexicon
exposure: continua, minimo 30-50%, maximo ate 120%
ranking: Calmar, Sharpe, Sortino, max drawdown
```

Resultado:

```text
aggressive: +19.33%, DD -7.82%, Sharpe 1.74, Sortino 2.31, Calmar 2.48
balanced:   +16.75%, DD -6.88%, Sharpe 1.74, Sortino 2.31, Calmar 2.45
defensive:  +10.73%, DD -4.81%, Sharpe 1.64, Sortino 2.12, Calmar 2.24

SPY:         +30.77%, DD -9.13%, Sharpe 2.23
QQQ:         +43.96%, DD -12.19%, Sharpe 2.34
```

Conclusao:

- O overlay continuo reduziu drawdown contra SPY/QQQ, mas ainda perdeu em retorno e Sharpe em janela bull market.
- O perfil agressivo teve melhor retorno bruto, mas o perfil balanceado ficou mais limpo em risco/retorno antes do tilt de sentimento.
- O sentimento real como tilt pequeno aumentou retorno bruto em perfis agressivo/balanceado, mas piorou Calmar por aumentar drawdown.
- Sentimento deve ser pequeno, condicional ou calibrado por grid; nao deve virar motor principal da exposicao.

## Rodada Growth Overlay SPY/QQQ

Periodo: aproximadamente 251 pregoes ate `2026-05-06`.

Configuracao:

```text
mode: growth-overlay
market-provider: yahoo
news-provider: yahoo
growth-symbols: SPY QQQ
benchmarks: SPY QQQ
exposure: continua, 50% ate 120%
selecao: momentum relativo SPY vs QQQ
```

Resultado:

```text
Growth overlay: +24.15%, DD -9.93%, Sharpe 1.76, Sortino 2.32
SPY:            +30.77%, DD -9.13%, Sharpe 2.23
QQQ:            +43.96%, DD -12.19%, Sharpe 2.34
```

Conclusao:

- A abordagem de upside capturou mais retorno que o overlay fixo.
- Ainda nao bateu SPY/QQQ em retorno ou Sharpe nessa janela bull.
- O modo growth deve ser otimizado separadamente do modo risk overlay.

## Decisoes Tomadas

- Alpaca foi descartada temporariamente por friccao de cadastro no Brasil.
- Alpha Vantage e Stooq foram removidos para reduzir superficie de manutencao; Yahoo virou a fonte real unica.
- Yahoo virou o provider recomendado sem chave.
- Noticias sinteticas nao podem ser usadas com dados reais.
- O agente aprende com feedback salvo em SQLite.
- O relatorio `explain` mostra explicabilidade por decisao usando contexto, sinal, status da ordem e razao textual.
- A estrategia agora usa `stop_loss`, `take_profit` e `trailing_stop`.
- O relatorio de efetividade mostra benchmarks salvos na simulacao, por padrao `SPY` e `QQQ`.
- O regime de mercado SPY/QQQ agora entra no score e bloqueia novas posicoes quando fica abaixo do minimo configurado.
- Saida por momentum do ativo existe, mas fica desativada por padrao porque aumentou frequencia sem melhorar edge na janela testada.
- Existe `--mode overlay`, que opera exposicao tática em um benchmark em vez de escolher acoes individuais.
- Existe `--mode growth-overlay`, que busca upside alternando SPY/QQQ por momentum relativo.
- O relatorio inclui Sharpe, Sortino e drawdown/Sharpe dos benchmarks.
- Pesquisa de desempenho deve sempre usar dados reais Yahoo e noticias reais Yahoo; smoke sintetico nao conta como resultado.
- O overlay continuo inclui sentimento real como tilt pequeno de exposicao. No grid de 1 ano, o peso de sentimento melhorou retorno bruto em perfis agressivo/balanceado, mas piorou Calmar por aumentar drawdown.

## Proximos Passos

- Criar uma especificacao de pesquisa para grid search real: parametros, janelas, ranking e formato de resultados.
- Avaliar performance em janelas maiores: 180 dias, 1 ano, 2 anos e 5 anos.
- Priorizar Calmar, Sharpe, Sortino e max drawdown acima de retorno bruto.
- Testar sentimento condicional: usar tilt de sentimento apenas em regime positivo ou apenas quando volatilidade estiver alta.
- Testar overlay dinamico de risco: reduzir exposicao mais agressivamente quando volatilidade subir e regime deteriorar, sem sair 100% do mercado.
