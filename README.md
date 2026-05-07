# TraderIA

Agente de paper trading para compra e venda de acoes com contexto de mercado, timing, sentimento, feedback e metricas de efetividade.

Este projeto e um MVP local. Ele nao executa ordens reais e nao constitui recomendacao financeira.

## O que ele faz

- Gera ou recebe contexto de mercado por ativo.
- Calcula sinais de tendencia, momentum, RSI, MACD, ATR e Bollinger Bands.
- Decide entre `BUY`, `SELL` e `HOLD` com pesos de sinal configuráveis e ajustáveis por dados.
- Executa as decisoes em uma carteira simulada com slippage, spread e fee realistas.
- Suporta sizing por Kelly criterion e stop loss dinamico via ATR.
- Salva snapshots, decisoes, ordens, posicoes e feedback em SQLite (WAL mode).
- Mede efetividade com retorno total, drawdown, Sharpe, Sortino, Calmar, taxa de acerto e profit factor.
- Valida parametros out-of-sample via walk-forward backtesting.
- Otimiza hiperparametros via grid search rankeado por Calmar ratio.

## Rodando

```powershell
python -m traderia.cli simulate --symbols AAPL MSFT NVDA --days 90
python -m traderia.cli report
python -m traderia.cli explain --limit 10
python -m traderia.cli attribution
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

## Custos de Execucao Realistas

Slippage e spread sao aplicados automaticamente em toda ordem. Os defaults sao:

```
slippage_pct = 0.001   (0.1% por trade)
spread_pct   = 0.0005  (bid-ask de 0.05%)
fee_pct      = 0.0005  (corretagem)
```

BUY paga `preco × (1 + slippage + spread/2)`. SELL recebe `preco × (1 - slippage - spread/2)`. Para ajustar:

```powershell
python -m traderia.cli simulate --symbols AAPL MSFT NVDA --days 90 --slippage-pct 0.002 --spread-pct 0.001
```

## Indicadores Tecnicos

O `ContextBuilder` calcula automaticamente para cada barra:

| Indicador | Janela | Campo |
|---|---|---|
| Medias moveis | short=5, long=20 | `short_ma`, `long_ma` |
| RSI | 14 | `rsi` |
| MACD histogram | EMA 12/26/9 | `macd_histogram` |
| ATR | 14 (EMA) | `atr` (tambem e a base da volatilidade) |
| Bollinger %b | 20 | `bb_pct` |

Todos os indicadores aparecem no `explain`:

```powershell
python -m traderia.cli explain --action BUY --limit 5
```

O filtro RSI e opcional: para bloquear entradas overbought, configure `rsi_overbought=70` em `AgentConfig`.

## Stop Loss Dinamico via ATR

Por padrao o stop e fixo em 3%. Para usar stop dinamico baseado em volatilidade real do ativo:

```powershell
python -m traderia.cli simulate --symbols AAPL MSFT NVDA --days 90 --use-atr-stop --atr-stop-multiplier 2.0
```

O stop e calculado como `preco_entrada − 2 × ATR_na_entrada`. Em ativos mais volateis, o stop fica mais largo e nao e acionado por ruido normal.

## Kelly Criterion

Para sizing proporcional ao historico de acertos da carteira:

```powershell
python -m traderia.cli simulate --symbols AAPL MSFT NVDA --days 90 --use-kelly
```

Usa quarter-Kelly alimentado pelos eventos de feedback do SQLite. Requer pelo menos uma simulacao anterior para ter historico.

## Validacao Walk-Forward

Para medir se os parametros generalizam fora da amostra:

```powershell
python -m traderia.cli validate --symbols AAPL MSFT NVDA --total-days 500 --train-window 252 --test-window 63 --step 21
```

Gera uma tabela de folds com Return%, MaxDD%, Sharpe, Calmar e WinRate%. Folds positivos / total indica robustez.

Com dados sinteticos todos os folds sao identicos por design. Para validacao real, use `--market-provider yahoo`.

## Otimizacao de Hiperparametros

Para encontrar os parametros com melhor Calmar ratio:

```powershell
python -m traderia.cli optimize --symbols AAPL MSFT NVDA --days 252
```

Faz grid search com 108 combinacoes de `min_confidence`, `trailing_stop_pct`, `stop_loss_pct` e `max_position_pct`. Imprime o ranking e os flags prontos para re-usar no `simulate`.

Para otimizar os pesos do sinal via regressao linear nos dados historicos:

```python
from traderia.optimizer import fit_signal_weights
from traderia.storage import SQLiteStore

weights = fit_signal_weights(SQLiteStore("data/traderia.sqlite3"))
# retorna dict: {"timing": 0.xx, "sentiment": 0.xx, ...}
```

## Atribuicao de Performance

Para ver P&L medio por tipo de saida e por simbolo:

```powershell
python -m traderia.cli attribution
```

Mostra quanto cada tipo de saida (stop_loss, trailing_stop, negative_reversal, etc.) contribuiu e quais eram os valores de timing, sentimento e regime nesses trades.

## Sentimento com LLM

O analisador padrao e lexico para permitir testes offline.

**Hybrid com OpenAI** (escala casos ambiguos para LLM):

```powershell
$env:OPENAI_API_KEY="sua-chave"
python -m traderia.cli simulate --symbols AAPL MSFT NVDA --days 90 --sentiment-provider hybrid
```

**Claude API** (usa `claude-haiku` por padrao, rapido e barato):

```powershell
$env:ANTHROPIC_API_KEY="sua-chave"
python -m traderia.cli simulate --symbols AAPL MSFT NVDA --days 90 --sentiment-provider claude
```

**Hybrid com Claude** (lexicon primeiro, Claude so quando necessario):

```powershell
$env:ANTHROPIC_API_KEY="sua-chave"
python -m traderia.cli simulate --symbols AAPL MSFT NVDA --days 90 --sentiment-provider hybrid-claude
```

Se a chave nao existir ou a chamada falhar, o agente volta automaticamente para o analisador lexico.

## Dados Reais com Yahoo

Para evitar limites de API durante testes, use o provider `yahoo`. Ele baixa historico diario gratuito para acoes dos EUA e o cache fica em `data/yahoo_cache`.

```powershell
python -m traderia.cli simulate --market-provider yahoo --news-provider yahoo --symbols AAPL MSFT NVDA --days 90 --reset-db
```

Ao usar `--news-provider auto`, noticias sinteticas so sao usadas com `--market-provider synthetic`. Com dados reais, o padrao automatico usa noticias reais do Yahoo.

Resultados de performance devem usar dados reais: `--market-provider yahoo --news-provider yahoo`.

A simulacao salva benchmarks `SPY` e `QQQ` automaticamente e usa esses benchmarks para calcular o regime de mercado antes de abrir posicoes.

## Modo Overlay

Para usar o TraderIA como controlador de exposicao em um ETF:

```powershell
python -m traderia.cli --db data\overlay.sqlite3 simulate --mode overlay --market-provider yahoo --news-provider yahoo --overlay-symbol SPY --days 251 --reset-db
```

O overlay nunca sai totalmente do mercado; varia a exposicao entre 30% e 120% conforme regime, momentum, sentimento e volatilidade.

Para buscar upside rotacionando entre SPY e QQQ por momentum relativo:

```powershell
python -m traderia.cli --db data\growth_overlay.sqlite3 simulate --mode growth-overlay --market-provider yahoo --news-provider yahoo --growth-symbols SPY QQQ --days 251 --reset-db
```

## Risco de Portfolio

O broker oferece duas metricas de concentracao:

```python
broker.portfolio_concentration_score(prices)  # Herfindahl index (0=diversificado, 1=concentrado)
broker.correlation_penalty(histories)          # correlacao media entre posicoes abertas
```

Essas metricas podem ser usadas para limitar novas posicoes quando a carteira ja esta concentrada.

## Explicabilidade

Para inspecionar por que o agente decidiu comprar, vender ou esperar:

```powershell
python -m traderia.cli explain --limit 20
python -m traderia.cli explain --action BUY --limit 5
```

O relatorio exibe: sinal esperado, timing, regime, sentimento, momentum, RSI, MACD histogram, ATR, Bollinger %b, volatilidade, volume relativo, medias moveis e razao textual.

## Proximos passos antes de dinheiro real

1. Rodar grids com `--market-provider yahoo` em janelas de 2 e 5 anos para validar Calmar e drawdown.
2. Executar `validate` com dados reais para medir consistencia entre periodos.
3. Calibrar pesos do sinal com `fit_signal_weights` em historico longo.
4. Definir limites de concentracao maxima antes de escalar posicoes.
5. Conectar um broker real (ex: Alpaca) somente apos validacao consistente no paper trade.
