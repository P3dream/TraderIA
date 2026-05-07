# TraderIA

Agente de paper trading para compra e venda de acoes com contexto de mercado, timing, sentimento, feedback e metricas de efetividade.

Este projeto e um MVP local. Ele nao executa ordens reais e nao constitui recomendacao financeira.

## O que ele faz

- Gera ou recebe contexto de mercado por ativo.
- Calcula sinais de tendencia, momentum, risco, timing e sentimento.
- Decide entre `BUY`, `SELL` e `HOLD`.
- Executa as decisoes em uma carteira simulada.
- Salva snapshots, decisoes, ordens, posicoes e feedback em SQLite.
- Mede efetividade com retorno total, retorno percentual, drawdown, Sharpe, Sortino, taxa de acerto e profit factor.

## Rodando

```powershell
python -m traderia.cli simulate --symbols AAPL MSFT NVDA --days 90
python -m traderia.cli report
python -m traderia.cli explain --limit 10
```

Por padrao, os dados ficam em `data/traderia.sqlite3`.

Para uma rodada limpa, apagando tambem a memoria de feedback:

```powershell
python -m traderia.cli simulate --symbols AAPL MSFT NVDA --days 90 --reset-db
```

Para testar aprendizado, rode uma primeira simulacao e depois rode outra apagando apenas as execucoes, mas preservando o feedback:

```powershell
python -m traderia.cli simulate --symbols AAPL MSFT NVDA --days 90 --reset-runs
```

## Sentimento com LLM

O analisador padrao ainda e lexico para permitir testes offline. Para usar o modo hibrido, que tenta resolver casos simples localmente e escala manchetes ambiguas ou relevantes para LLM, defina a chave e escolha o provider:

```powershell
$env:OPENAI_API_KEY="sua-chave"
python -m traderia.cli simulate --symbols AAPL MSFT NVDA --days 90 --sentiment-provider hybrid --sentiment-model gpt-5
```

Tambem e possivel forcar sempre LLM com `--sentiment-provider openai`. Se a chave nao existir ou a chamada falhar, o agente volta automaticamente para o analisador lexico.

## Dados Reais sem Chave com Yahoo

Para evitar limites de API durante testes, use o provider `yahoo`. Ele baixa historico diario gratuito para acoes dos EUA e o TraderIA continua executando paper trade local.

```powershell
python -m traderia.cli simulate --market-provider yahoo --news-provider yahoo --symbols AAPL MSFT NVDA --days 90 --reset-db
```

O cache fica em `data/yahoo_cache`.

Ao usar `--news-provider auto`, noticias sinteticas so sao usadas com `--market-provider synthetic`. Com dados reais, o padrao automatico usa noticias reais do Yahoo. Para desligar sentimento por noticia, use `--news-provider none`.

Resultados de performance devem usar dados reais e noticias reais: `--market-provider yahoo --news-provider yahoo`. Dados sinteticos ficam apenas para testes offline e smoke tecnico.

Por padrao, a simulacao tambem salva benchmarks `SPY` e `QQQ`, exibidos no `report`, e usa esses benchmarks para calcular um regime de mercado antes de abrir posicoes. Para ajustar frequencia e risco:

```powershell
python -m traderia.cli simulate --market-provider yahoo --news-provider yahoo --symbols AAPL MSFT NVDA --days 90 --min-confidence 0.50 --stop-loss-pct 0.03 --take-profit-pct 0 --trailing-stop-pct 0.10 --min-market-regime -0.10 --reset-db
```

Para testar uma saida mais ativa por perda de momentum do ativo, habilite explicitamente:

```powershell
python -m traderia.cli simulate --market-provider yahoo --news-provider yahoo --symbols AAPL MSFT NVDA --days 90 --momentum-exit-threshold -0.005 --reset-db
```

## Modo Overlay

Para testar o TraderIA como controlador de exposicao em um ETF, use `--mode overlay`. Nesse modo ele nao escolhe acoes individuais; ele rebalanceia o `--overlay-symbol` com exposicao continua. O padrao atual nunca sai totalmente do mercado e varia a exposicao entre 30% e 120%.

```powershell
python -m traderia.cli --db data\yahoo_overlay_1y.sqlite3 simulate --mode overlay --market-provider yahoo --news-provider yahoo --overlay-symbol SPY --benchmark-symbols SPY QQQ --days 251 --reset-db
```

O `report` mostra retorno, drawdown, Sharpe, Sortino e os mesmos indicadores dos benchmarks.

O overlay continuo usa regime de mercado, momentum, sentimento real e penalidade de volatilidade para calcular a exposicao alvo.

Para buscar upside, use `--mode growth-overlay`. Nesse modo o TraderIA alterna entre os ativos em `--growth-symbols` por momentum relativo, por padrao `SPY` e `QQQ`, mantendo a mesma logica de exposicao continua.

```powershell
python -m traderia.cli --db data\growth_overlay_1y.sqlite3 simulate --mode growth-overlay --market-provider yahoo --news-provider yahoo --growth-symbols SPY QQQ --benchmark-symbols SPY QQQ --days 251 --reset-db
```

Para pesquisar parametros de overlay com ranking por Calmar/Sharpe/DD, use o skill local `traderia-research`:

```powershell
python C:\Users\pedro\.codex\skills\traderia-research\scripts\run_overlay_grid.py --repo C:\Users\pedro\Desktop\Code\TraderIA --days 251 --market-provider yahoo --news-provider yahoo
```

## Explicabilidade

Para inspecionar por que o agente decidiu comprar, vender ou esperar, use:

```powershell
python -m traderia.cli --db data\yahoo_real_news_paper.sqlite3 explain --limit 20
python -m traderia.cli --db data\yahoo_real_news_paper.sqlite3 explain --action BUY --limit 5
```

O relatorio junta decisao, contexto de mercado e status da ordem, exibindo sinal esperado, timing, sentimento, momentum, volatilidade, volume relativo, medias moveis e razao textual da decisao.

## Proximos encaixes

- Melhorar sentimento real sem custo pago e testar pesos condicionais de sentimento no overlay.
- Rodar grids reais em janelas de 2 anos e 5 anos, priorizando Calmar, Sharpe e drawdown.
- Agendar execucoes em horario de mercado.
- Conectar um broker real somente depois de validar o paper trade.
