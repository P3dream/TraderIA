# TraderIA Research Spec

## Objetivo

Avaliar o TraderIA como `risk overlay` para SPY/QQQ usando somente dados reais Yahoo e noticias reais Yahoo.

O objetivo principal nao e maximizar retorno bruto. O objetivo e encontrar configuracoes que melhorem risco ajustado e preservem capital em janelas ruins.

## Regras

- Usar `--market-provider yahoo`.
- Usar `--news-provider yahoo`.
- Nao usar dados sinteticos como evidencia de desempenho.
- Ranqueamento principal: Calmar, Sharpe, Sortino e max drawdown.
- Retorno bruto e criterio secundario.
- Nao permitir saida total do mercado no overlay; a exposicao minima deve ser maior que zero.
- Registrar a janela exata em pregoes, porque o Yahoo pode retornar menos barras que o numero teorico.

## Comando Base

```powershell
python C:\Users\pedro\.codex\skills\traderia-research\scripts\run_overlay_grid.py --repo C:\Users\pedro\Desktop\Code\TraderIA --days 251 --market-provider yahoo --news-provider yahoo
```

## Perfis Iniciais

```text
defensive:
  min exposure: 0.30
  max exposure: 1.00
  base exposure: 0.60
  regime weight: 0.30
  momentum weight: 0.15
  volatility weight: 0.25
  sentiment weight: 0.03

balanced:
  min exposure: 0.50
  max exposure: 1.20
  base exposure: 0.80
  regime weight: 0.25
  momentum weight: 0.25
  volatility weight: 0.15
  sentiment weight: 0.05

aggressive:
  min exposure: 0.50
  max exposure: 1.20
  base exposure: 0.90
  regime weight: 0.30
  momentum weight: 0.25
  volatility weight: 0.10
  sentiment weight: 0.05
```

## Metricas

```text
total_return_pct
max_drawdown_pct
sharpe_ratio
sortino_ratio
calmar_ratio
closed_trades
benchmark_return_pct
benchmark_max_drawdown_pct
benchmark_sharpe_ratio
```

Calmar:

```text
annualized_return_pct / abs(max_drawdown_pct)
```

## Resultados Atuais

Janela: aproximadamente 251 pregoes ate `2026-05-06`.

```text
aggressive: +19.33%, DD -7.82%, Sharpe 1.74, Sortino 2.31, Calmar 2.48
balanced:   +16.75%, DD -6.88%, Sharpe 1.74, Sortino 2.31, Calmar 2.45
defensive:  +10.73%, DD -4.81%, Sharpe 1.64, Sortino 2.12, Calmar 2.24

SPY:         +30.77%, DD -9.13%, Sharpe 2.23
QQQ:         +43.96%, DD -12.19%, Sharpe 2.34
```

Leitura:

- O overlay reduz drawdown, mas ainda perde em retorno e Sharpe em bull market.
- O tilt de sentimento aumentou retorno bruto, mas piorou Calmar por aumentar drawdown.
- Sentimento deve ser testado como sinal condicional, nao como peso fixo alto.

## Proximos Experimentos

1. Rodar 2 anos e 5 anos com os tres perfis atuais.
2. Testar `overlay_sentiment_weight` em `0.00`, `0.02`, `0.05` e `0.10`.
3. Testar sentimento condicional:
   - aplicar sentimento apenas quando regime for positivo;
   - aplicar sentimento apenas quando volatilidade for alta;
   - ignorar sentimento quando regime e momentum divergirem.
4. Testar volatilidade dinamica:
   - aumentar penalidade de volatilidade em regime negativo;
   - reduzir penalidade de volatilidade em regime positivo.
5. Comparar contra buy-and-hold SPY e QQQ em todas as janelas.
6. Manter duas familias separadas:
   - `overlay`: reduzir risco em um benchmark fixo.
   - `growth-overlay`: buscar upside alternando SPY/QQQ por momentum relativo.

## Growth Overlay

Objetivo: capturar mais upside que o overlay defensivo aceitando mais drawdown.

Comando base:

```powershell
python -m traderia.cli --db data\growth_overlay_1y.sqlite3 simulate --mode growth-overlay --market-provider yahoo --news-provider yahoo --sentiment-provider lexicon --growth-symbols SPY QQQ --benchmark-symbols SPY QQQ --days 251 --overlay-min-exposure 0.50 --overlay-max-exposure 1.20 --overlay-base-exposure 0.90 --overlay-regime-weight 0.30 --overlay-momentum-weight 0.25 --overlay-sentiment-weight 0.05 --overlay-volatility-weight 0.10 --reset-db
```

Resultado inicial em 251 pregoes ate `2026-05-06`:

```text
growth-overlay: +24.15%, DD -9.93%, Sharpe 1.76, Sortino 2.32
SPY:            +30.77%, DD -9.13%, Sharpe 2.23
QQQ:            +43.96%, DD -12.19%, Sharpe 2.34
```

Leitura:

- Melhorou upside contra overlay fixo.
- Ainda nao bate SPY/QQQ em retorno ou Sharpe na janela bull.
- Deve ser testado com outros `growth_momentum_window` e `growth_switch_margin`.
