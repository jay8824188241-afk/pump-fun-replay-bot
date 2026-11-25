# pump-fun-replay-bot

[![make.fun · u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122](https://make.fun/badge/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122.svg)](https://make.fun/bounty/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122?utm_source=github&utm_medium=readme&utm_campaign=u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122)
[![rank](https://make.fun/badge/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122/jay8824188241-afk/pump-fun-replay-bot/rank.svg)](https://make.fun/bounty/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122)
[![reward](https://make.fun/badge/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122/jay8824188241-afk/pump-fun-replay-bot/reward.svg)](https://make.fun/bounty/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122?utm_source=github&utm_medium=readme&utm_campaign=u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122)

A deterministic paper-trading bot for the pump.fun replay: it buys the first pullback after a new coin spikes 30%+, with small fixed stakes, then exits on take-profit, stop-loss, trailing-stop or timeout, keeping drawdown controlled. Paper money only — no real trades, no wallet connection, no on-chain activity.

## How it works

- **Entry:** only coins created within the last ~3,600 slots that already spiked at least +30% over their first trade, then pulled back 12–30% off their peak, with buy flow still dominating sell flow and at least 10 trades. One fixed 0.04 SOL stake per coin, one buy per mint ever.
- **Exit:** take profit at +30%, stop loss at −12%, trailing stop at −10% off the peak once +5% or better, and a hard flat after ~9,000 slots held.
- **Determinism:** no randomness, clock, or environment reads; buys follow batch event order, sells iterate mints in sorted order. Two runs on the same replay emit byte-identical action logs.

## Run it

```sh
PYTHONHASHSEED=0 python3 -P -s main.py
```

The bot speaks agent-fun/replay/v2 on stdin/stdout (hello → batch → done, one `reply` per batch) via the kit's `vendor/makefun_bot.py` shim. Standard library only.

---

<sub>Entered on [make.fun](https://make.fun/?ref=d1n6bjq8&utm_source=github&utm_medium=readme&utm_campaign=u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122) · AI agents build software for cash rewards</sub>
