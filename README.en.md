# Cognitive Economy — An Investment Agent That Earns What It Spends

**Google Cloud × Solana AI Agentic Hackathon · Track A (Agent-Initiated Commerce)**

[한국어](README.md) · [Verification](VERIFICATION.md) · [Work log](WEB-STATUS.md) · [Deployment](deploy/README.md)

---

## In one sentence

You choose only a **market**. From there the bot **pays for its own** news and
inference, **actually trades** stocks on that judgement, and **earns back** the
cognitive costs it spent. A human signs exactly **once**.

---

## What is actually real

The principle we held to most carefully is that **every number on screen comes
from a real record.** No mock figures remain anywhere in the app.

| Area | What it actually uses |
|---|---|
| **Payments** | Solana **devnet** — one on-chain transaction per API call |
| **Quotes** | **Korea Investment & Securities (KIS) OpenAPI** — live domestic and overseas prices |
| **Inference** | **Google Gemini** — a real model makes the buy/sell decision |
| **News** | Alpha Vantage — same-day articles when you ask about a ticker in chat |
| **FX** | open.er-api.com — KRW and JPY figures convert at live rates |

When something is switched off, the screen says so. If inference fails and
falls back to the built-in mock judgement, the badge changes to `모의 판단`
(mock) and the resulting receipt is **refused for sale**.

### Tradable markets — 76 mirror stock tokens

| Market | Tickers | Examples |
|---|---|---|
| 🇰🇷 KOSPI | 19 | Samsung Electronics · SK hynix · Hyundai Motor |
| 🇰🇷 KOSDAQ | 20 | EcoPro BM · Alteogen · Rainbow Robotics |
| 🇺🇸 NASDAQ | 19 | AAPL · MSFT · NVDA · META |
| 🇯🇵 Tokyo | 18 | Toyota · Sony · Nintendo |

These are **mirror stocks** issued as SPL tokens on devnet. Because they are
bought with a stablecoin, **four countries' equities sit in one wallet with no
currency conversion** — that is the point of the design, and country flags
appear throughout the UI to make it visible.

The ticker list was not typed by hand. `check_markets.py` queries KIS for each
candidate and keeps **only those that return a price**; `mint_markets.py` then
issues mints for exactly those. A market is shown as tradable only when the
quote **and** the mint both exist.

---

## Core technology

### 1. x402 — pay-per-call over HTTP 402

The agent pays every time it buys data. Not a subscription — **per call**.

```
request → 402 challenge (price, payee) → policy check → on-chain payment
        → retry with proof → proof consumed once → data
```

Proofs are **bound to a resource and single-use.** Replaying a proof or
pointing it at a different endpoint is blocked in code, and the audit script
checks this every run.

### 2. The rulebook — the constitution is code

The rules you set (allowed markets, confidence floor, per-trade cap, stop-loss,
take-profit, daily limit) hold the **final veto**. However high the model calls
its confidence, nothing executes below the floor.

> One exception: **an order you give directly in chat.** The rulebook governs
> the agent's autonomous decisions, not the owner's manual orders — the same
> way a brokerage app lets you place a market order regardless of your standing
> automation rules. Such fills are recorded as `manual` on the receipt and are
> excluded from the agent's hit-rate statistics.

### 3. Mandates — automatic approval, automatic refusal

When the bot needs money it **issues its own invoice**, and policy reviews it.

| | Cognitive mandate | Capital mandate |
|---|---|---|
| Invoice | research-agent → revenue-wallet | invest-wallet → user-treasury |
| Refused when | ROI floor · weekly cap · no funds | **hit rate below 40%** · total exposure cap |

What matters is that refusals actually happen. This is a **review**, not a
standing debit.

### 4. Receipts — sellable only if provable

Every decision leaves a receipt: which news it read, which model answered, and
which payment proofs back it. The receipt records **the model that actually
answered**, not the mode we declared — so a fallback is exposed by the receipt
itself, and that judgement cannot be sold.

### 5. Delegation — the human signs once

An SPL `approve` grants an allowance. After that the agent executes withdrawals,
payments and trades **with no further signature**. It is the same shape as a
direct-debit authorisation, and the chain rejects anything above the allowance —
so you can see for yourself that it is not a blank cheque.

### 6. Session isolation

Each browser gets a session ID that owns its bots and wallet. Several people can
open the same link without seeing each other's bots, and knowing the admin token
does not let anyone touch someone else's.

---

## The app

Six Figma screens were ported and wired to the real backend. On desktop it runs
inside an iPhone mock-up.

- **Boot sequence** iOS home screen → splash → app (as if opened inside a bank app)
- **Home** total assets · bot cards · top-up
- **Bot detail** AI summary report · equity curve · holdings / trade history / API spend
- **Chat** ask the bot, or instruct it to trade (it declines non-investment topics)
- **Bot settings** market · prompt · rulebook, plus the exact AI instructions stored

A **step-by-step guide** and a **live execution log** sit permanently on the
right, so a first-time viewer can follow along and see what is happening.

---

## What you need to run it

### Prerequisites

| Item | Purpose | Without it |
|---|---|---|
| Python 3.13 | backend | required |
| Node.js 22 | frontend build | required |
| **Solana devnet wallet + SOL** | on-chain payments and fees | devnet mode will not start |
| **Gemini API key** | real inference | falls back to mock judgement |
| **KIS appkey + appsecret** | live quotes | falls back to built-in base prices |
| Alpha Vantage key (optional) | news in chat | answers without news |

> KIS keys are issued at [KIS Developers](https://apiportal.koreainvestment.com).
> **Both appkey and appsecret are required**, and the access token can only be
> issued once per minute, so it is cached to a file.

### Environment (`.env`)

```ini
INFERENCE_MODE=byok
GEMINI_API_KEY=...
GEMINI_FLASH_MODEL=gemini-3.1-flash-lite
GEMINI_DEEP_MODEL=gemini-3.1-flash-lite

KIS_APP_KEY=...
KIS_APP_SECRET=...
KIS_ENV=real

ALPHAVANTAGE_API_KEY=...
```

### First-time setup

```powershell
py -3.13 -m pip install -r requirements.txt

py -3.13 setup_wallets.py       # create devnet wallets
py -3.13 bootstrap_devnet.py    # issue USDC and mirror tokens, fund wallets
py -3.13 check_markets.py       # validate tickers against KIS
py -3.13 mint_markets.py        # mint tokens for validated tickers
```

Devnet SOL comes from [faucet.solana.com](https://faucet.solana.com). Only the
fee-payer wallet needs it.

### Running

```powershell
# 1) backend
$env:LEDGER_MODE="devnet"; $env:PYTHONIOENCODING="utf-8"
py -3.13 -m uvicorn app.main:app --port 8100

# 2) frontend
cd web; npm install; npm run dev      # → http://localhost:5173
```

> Without `PYTHONIOENCODING=utf-8` the server dies while printing its startup
> log on a cp949 console. This bites often on Windows, so it is worth setting.

### Mode switches

The three axes are independent — changing one leaves the others alone.

```powershell
$env:INFERENCE_MODE="byok"      # inference: your Gemini key (recommended)
$env:INFERENCE_MODE="mock"      # inference: built-in mock (works with no key)

$env:LEDGER_MODE="devnet"       # ledger: real SPL transfers
$env:LEDGER_MODE="mock"         # ledger: in-memory

$env:PRICE_SOURCE="kis"         # quotes: KIS live prices (default)
$env:PRICE_SOURCE="mock"        # quotes: built-in base prices (used by the verifier)
```

---

## Verification

Claims are proved by **execution**, not prose.

```powershell
py -3.13 verify_scenario.py    # full scenario
py -3.13 audit.py              # security audit
```

| Check | Result |
|---|---|
| `verify_scenario.py` (mock) | **23 passed / 0 failed** / 2 n/a |
| `verify_scenario.py` (devnet) | **25 passed / 0 failed** |
| `audit.py` | **all 9 items passed** |

A few of the things it proves:

- There is **no route at all** by which user principal can leak into API costs
- Reusing a payment proof, or using it on another endpoint, is **refused**
- The three settlement legs **share a single on-chain transaction**
- A failed confirmation lookup still results in **exactly one** transfer
- Bots and funds **survive a server restart**

Item-by-item evidence is in [VERIFICATION.md](VERIFICATION.md).

---

## Layout

```
app/
  main.py            trading, settlement, manual orders, frontend serving
  ui.py              app-facing read API (/ui/*) — never moves money
  judge.py           wallet delegation demo (/judge/*)
  agents/pipeline.py scout → analyst → executor
  core/
    routes.py        wallet route whitelist  ← where fund leakage is blocked
    mandate.py       invoice review (auto approve / refuse)
    receipts.py      receipts, settlement split, provenance honesty
    positions.py     position book + rulebook exit rules
    markets.py       market catalogue (validated tickers only)
    session.py       per-browser isolation
    prompts.py       rulebook → AI instructions
    journal.py       fills, equity curve, API calls
    store.py         restart survival
  adapters/
    devnet_ledger.py Solana devnet ledger (atomic settlement, idempotent retry)
    kis_quotes.py    KIS live quotes
    gemini_byok.py   Gemini inference
    news.py          ticker news
    fx.py            exchange rates
web/                 React + TypeScript (Vite)
```

Each bot holds four wallets separated by role, and the routes between them are
fixed by a whitelist.

```
user-treasury   principal      ◀──────┐ 85% of profit
     │ capital mandate                │
     ▼                                │
invest-wallet   trading funds  ───────┤ 10%
     │ swap_in/out                    │
     ▼                                │
  [market] mirror stock tokens        │
                                      │
revenue-wallet  sales income   ◀──────┘ 5%
     │ cognitive mandate
     ▼
research-agent  cognitive budget   starts at zero — funded only by invoices
     │ x402 payments
     ▼
news · screening · deep inference · quotes · other bots' signals
```

There is **no `user-treasury → research-agent` route.** User principal cannot
leak into API costs structurally, and the audit confirms it.

---

## Deployment

FastAPI serves the API **and** the UI, so it is a single service. Full steps are
in [deploy/README.md](deploy/README.md).

```powershell
py -3.13 deploy/pack_secrets.py    # bundle wallets and keys (never committed)
docker build -t cognitive-economy .
```

Three things to watch:

1. **Run exactly one worker.** Bots and positions live in process memory.
2. **Put `wallets/` on a persistent volume.** Losing it strands devnet funds
   with no key left to move them.
3. **HTTPS is required.** Phantom only injects in a secure context.

---

## Limitations we state plainly

- These are **mirror stock tokens**, not real equity fills — SPL tokens on
  devnet, with only the prices being real. Korean capital-markets law has not
  settled this area, so we deliberately stopped here; real execution would be
  enabled by swapping the adapter.
- **Headlines in the trading cycle are synthetic.** Ticker symbols and company
  names are real (verified via KIS and SEC lists), but the headline sentences
  are generated. Articles fetched during chat are real.
- **Session isolation is not authentication.** Anyone who copies a session ID
  can impersonate it. It isolates reviewers on a demo link; production would
  need server-side sessions and login.
- **The admin token ships in the browser bundle.** That is a demo arrangement;
  the real protection is session isolation.
- **There is no withdrawal path.** Deleting a bot leaves its balance in its own
  wallets — the same rule that prevents principal leakage — and the app states
  this clearly before deletion. (Settlement back to a connected wallet is a
  separate, permitted route.)
- **Shinhan Bank Reports and Toss stock-forum data** appear in the recommended
  API list but are **not connected**. There is no adapter and no partnership;
  they are shown as `준비 중` (planned).

---

## Related documents

| Document | Contents |
|---|---|
| [VERIFICATION.md](VERIFICATION.md) | Execution evidence for all 25 checks |
| [WEB-STATUS.md](WEB-STATUS.md) | Problems hit during development and how they were solved |
| [web/README.md](web/README.md) | Frontend implementation notes |
| [deploy/README.md](deploy/README.md) | Deployment procedure |
| [AUDIT.md](AUDIT.md) | Security audit items |
