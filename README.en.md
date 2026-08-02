# Cognitive Economy

**An investment agent that earns what it spends**
Google Cloud × Solana AI Agentic Hackathon · Track A

**[For reviewers](JUDGE.md)** · [한국어](README.md) · [Verification](VERIFICATION.md) · [Deployment](deploy/README.md)

---

## What it is

You pick a **market**. The bot then **pays for** its own news and inference,
**actually trades** on that judgement, and **earns back** what it spent.
A human signs exactly **once**.

```
wallet delegation (one signature)
      ↓
buy news → screening → deep inference → rulebook → fill → settle
  $0.002    $0.00015      $0.0056                       85/10/5 split
  └──────── all paid on-chain via x402 · no signatures ────────┘
```

---

## What is real

| | What it uses |
|---|---|
| **Payments** | Solana devnet — one API call = one on-chain transaction |
| **Quotes** | Korea Investment & Securities (KIS) OpenAPI — live, domestic and overseas |
| **Inference** | Google Gemini — a real model makes the buy/sell call |
| **News** | Alpha Vantage — same-day articles, read during chat |
| **FX** | open.er-api.com — live KRW and JPY conversion |

Every figure on screen is computed from the ledger and journal. If something is
switched off, the UI says so.

### Markets — 76 mirror stock tokens

| Market | Tickers | Examples |
|---|---|---|
| 🇰🇷 KOSPI | 19 | Samsung Electronics · SK hynix · Hyundai Motor |
| 🇰🇷 KOSDAQ | 20 | EcoPro BM · Alteogen |
| 🇺🇸 NASDAQ | 19 | AAPL · MSFT · NVDA |
| 🇯🇵 Tokyo | 18 | Toyota · Sony · Nintendo |

Mirror stocks issued as SPL tokens on devnet, with **real prices**. Because they
are bought with a stablecoin, **four countries' equities live in one wallet with
no currency conversion**.

---

## How it works

| | |
|---|---|
| **x402** | Pay on every data purchase. 402 challenge → on-chain payment → retry with proof. Proofs are bound to a resource and **single-use** |
| **Rulebook** | Your rules hold the **final veto**. However high the model's confidence, nothing executes below the floor |
| **Mandates** | The bot issues its own invoice and policy reviews it. Below a 40% hit rate, **further funding is refused** |
| **Receipts** | Records which news and which model answered. If it fell back, that judgement **cannot be sold** |
| **Delegation** | One SPL `approve`, then payments and trades run without further signatures. The chain rejects anything above the allowance |
| **Sessions** | Each browser gets its own bots and wallet. Several reviewers can share one link without mixing |

Each bot holds four role-separated wallets with a fixed route whitelist. There is
**no `user-treasury → research-agent` route** — user principal cannot leak into
API costs.

---

## The app

Six Figma screens wired to the real backend, running inside an iPhone mock-up on
desktop.

**Home** assets · bot cards · top-up · **Bot detail** AI report · equity curve ·
trades · API spend · **Chat** questions and orders · **Settings** market · prompt ·
rulebook

A **step-by-step guide** and a **live execution log** sit permanently on the right.

---

## Running it

### Prerequisites

| | Purpose | Without it |
|---|---|---|
| Python 3.13 · Node.js 22 | run · build | required |
| Solana devnet wallet + SOL | on-chain payments | devnet will not start |
| Gemini API key | real inference | falls back to mock |
| KIS appkey + appsecret | live quotes | falls back to base prices |
| Alpha Vantage key (optional) | news in chat | answers without news |

> KIS keys come from [KIS Developers](https://apiportal.koreainvestment.com).
> **Both appkey and appsecret** are required.

### `.env`

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
py -3.13 bootstrap_devnet.py    # issue USDC and mirror tokens
py -3.13 check_markets.py       # validate tickers against KIS
py -3.13 mint_markets.py        # mint the validated ones
```

Devnet SOL: [faucet.solana.com](https://faucet.solana.com)

### Run

```powershell
# backend
$env:LEDGER_MODE="devnet"; $env:PYTHONIOENCODING="utf-8"
py -3.13 -m uvicorn app.main:app --port 8100

# frontend
cd web; npm install; npm run dev      # → http://localhost:5173
```

> Without `PYTHONIOENCODING=utf-8` the server dies printing its startup log on a
> cp949 console.

### Mode switches

Three independent axes.

```powershell
$env:INFERENCE_MODE="byok"   # byok (your key) · mock
$env:LEDGER_MODE="devnet"    # devnet (real SPL) · mock
$env:PRICE_SOURCE="kis"      # kis (live) · mock
```

---

## Verification

```powershell
py -3.13 verify_scenario.py    # mock 23/0 · devnet 25/0
py -3.13 audit.py              # security audit 9/9
```

Among the things it proves:

- There is **no route** by which user principal reaches API costs
- Replaying or cross-using a payment proof is **refused**
- The three settlement legs share **one on-chain transaction**
- A failed confirmation lookup still transfers **exactly once**
- Bots and funds **survive a restart**

Evidence per item → [VERIFICATION.md](VERIFICATION.md)

---

## Layout

```
app/
  main.py     trading · settlement · manual orders · serves the frontend
  ui.py       app API (/ui/*)        judge.py  wallet delegation (/judge/*)
  core/       routes(rules) · mandate(review) · receipts
              markets · session(isolation) · prompts(AI instructions)
  adapters/   devnet_ledger · kis_quotes · gemini_byok · news · fx
web/          React + TypeScript (Vite)
```

---

## Documents

| | |
|---|---|
| [JUDGE.md](JUDGE.md) | Reviewer guide — what to prepare, what to expect |
| [VERIFICATION.md](VERIFICATION.md) | Evidence for all 25 checks |
| [deploy/README.md](deploy/README.md) | Deployment |
| [WEB-STATUS.md](WEB-STATUS.md) | Development log |
| [web/README.md](web/README.md) | Frontend notes |
