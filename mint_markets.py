"""검증된 종목의 미러 토큰을 devnet 에 발행한다.

    py -3.13 check_markets.py     # 먼저 KIS 로 종목을 검증하고
    py -3.13 mint_markets.py      # 그 결과대로 민트를 만든다

[무엇을 하나]
  ① wallets/market_candidates.json 에서 '시세가 실제로 오는' 종목만 읽는다
  ② 아직 민트가 없는 종목에 대해 SPL 민트를 만든다
  ③ market 지갑의 토큰 계정을 만들고 유동성을 발행한다
  ④ wallets/devnet.json 의 mirror_mints 를 갱신한다

[왜 bootstrap_devnet.py 와 따로인가]
bootstrap 은 '처음 한 번' 전체를 세우는 스크립트다. 여기는 이미 도는
시스템에 종목만 **더한다** — 기존 민트·잔고·봇을 건드리지 않는다.
이미 있는 종목은 건너뛰므로 몇 번을 돌려도 같은 상태가 된다.

[봇 지갑의 토큰 계정은 만들지 않는다]
매수하는 순간 swap_in 이 그 종목 계정만 멱등 생성한다. 여기서 봇마다
전 종목 계정을 파면 80종 × 봇 수만큼 임대료가 나간다.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys

WALLET_DIR = pathlib.Path("wallets")
CONFIG_PATH = WALLET_DIR / "devnet.json"
CANDIDATES = WALLET_DIR / "market_candidates.json"
USDC_DECIMALS = 6

# 시장 하나당 market 지갑에 넣어둘 유동성. 미러 토큰이라 '시장이 팔 수 있는
# 물량'일 뿐이고, 사용자 자산이 아니다. 넉넉하되 무한은 아니다.
SUPPLY = 1_000 * 10**USDC_DECIMALS


def die(msg: str) -> None:
    print(f"\n❌ {msg}")
    raise SystemExit(1)


async def main() -> None:
    from solana.rpc.async_api import AsyncClient
    from solana.rpc.commitment import Confirmed
    from solders.keypair import Keypair
    from solders.message import MessageV0
    from solders.pubkey import Pubkey
    from solders.system_program import CreateAccountParams, create_account
    from solders.transaction import VersionedTransaction
    from spl.token.constants import TOKEN_PROGRAM_ID
    from spl.token.instructions import (
        create_idempotent_associated_token_account,
        get_associated_token_address, initialize_mint, mint_to)
    from spl.token.models import InitializeMintParams, MintToParams

    if not CANDIDATES.exists():
        die(f"{CANDIDATES} 없음 — py -3.13 check_markets.py 를 먼저 돌리세요.")
    if not CONFIG_PATH.exists():
        die(f"{CONFIG_PATH} 없음 — bootstrap_devnet.py 를 먼저 돌리세요.")

    cand = json.loads(CANDIDATES.read_text(encoding="utf-8"))
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    mirrors: dict[str, str] = dict(cfg["mirror_mints"])
    registry = json.loads((WALLET_DIR / "registry.json").read_text())

    # 발행 대상: 검증 통과했는데 아직 민트가 없는 것.
    wanted: list[tuple[str, str]] = []           # (미러티커, 사람이름)
    for market in cand.values():
        for row in market["ok"]:
            mt = f"{row['ticker']}x"
            if mt not in mirrors and all(mt != w[0] for w in wanted):
                wanted.append((mt, row["name"]))

    print(f"검증 통과 종목 {sum(len(m['ok']) for m in cand.values())}종 · "
          f"기존 민트 {len(mirrors)}종 · 새로 발행할 것 {len(wanted)}종")
    if not wanted:
        print("새로 만들 것이 없습니다.")
        return

    payer = Keypair.from_bytes(bytes(json.loads(
        pathlib.Path(cfg["fee_payer_path"]).read_text())))
    market_owner = Pubkey.from_string(registry["market"])
    client = AsyncClient(cfg["rpc"])

    lamports = (await client.get_balance(payer.pubkey())).value
    # 종목당 민트 임대료(≈0.00146) + market ATA(≈0.00204) + 수수료
    need_sol = len(wanted) * 0.004
    print(f"수수료 지갑 {payer.pubkey()} · SOL {lamports/1e9:.4f} "
          f"(필요 추정 {need_sol:.3f})")
    if lamports / 1e9 < need_sol:
        await client.close()
        die(f"SOL 부족 — {need_sol:.3f} 이상 필요합니다. "
            f"https://faucet.solana.com 에서 받아 {payer.pubkey()} 로 보내세요.")

    async def send(ixs, signers, retries: int = 6):
        last = None
        for attempt in range(retries):
            try:
                bh = (await client.get_latest_blockhash()).value.blockhash
                msg = MessageV0.try_compile(payer.pubkey(), ixs, [], bh)
                sig = (await client.send_transaction(
                    VersionedTransaction(msg, signers))).value
                await client.confirm_transaction(sig, commitment=Confirmed)
                await asyncio.sleep(0.8)
                return sig
            except Exception as e:                        # noqa: BLE001
                last = e
                if attempt == retries - 1:
                    break
                wait = 3 * (attempt + 1)
                print(f"     ⏳ 재시도 {attempt+1}/{retries} ({wait}s): {str(e)[:70]}")
                await asyncio.sleep(wait)
        raise RuntimeError(f"{retries}회 실패: {last}")

    rent = (await client.get_minimum_balance_for_rent_exemption(82)).value
    done = 0
    for ticker, name in wanted:
        try:
            mint_kp = Keypair()
            # 민트 생성 + 초기화 + market 계정 + 유동성 발행을 한 트랜잭션에.
            # 쪼개면 중간에 끊겼을 때 '민트는 있는데 물량이 없는' 종목이 남는다.
            ata = get_associated_token_address(market_owner, mint_kp.pubkey())
            await send([
                create_account(CreateAccountParams(
                    from_pubkey=payer.pubkey(), to_pubkey=mint_kp.pubkey(),
                    lamports=rent, space=82, owner=TOKEN_PROGRAM_ID)),
                initialize_mint(InitializeMintParams(
                    program_id=TOKEN_PROGRAM_ID, mint=mint_kp.pubkey(),
                    decimals=USDC_DECIMALS, mint_authority=payer.pubkey())),
                create_idempotent_associated_token_account(
                    payer.pubkey(), market_owner, mint_kp.pubkey()),
                mint_to(MintToParams(
                    program_id=TOKEN_PROGRAM_ID, mint=mint_kp.pubkey(),
                    dest=ata, mint_authority=payer.pubkey(), amount=SUPPLY)),
            ], [payer, mint_kp])

            mirrors[ticker] = str(mint_kp.pubkey())
            done += 1
            print(f"  ✅ {ticker:10} {name[:16]:18} {mint_kp.pubkey()}")

            # 한 종목이 끝날 때마다 저장한다. 중간에 끊겨도 여기까지는 남고,
            # 다시 돌리면 이어서 진행된다.
            cfg["mirror_mints"] = mirrors
            CONFIG_PATH.write_text(
                json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as e:                            # noqa: BLE001
            print(f"  ❌ {ticker:10} 실패: {str(e)[:110]}")

    await client.close()
    print(f"\n발행 완료 {done}/{len(wanted)}종 · 총 미러 민트 {len(mirrors)}종")
    print(f"설정 저장 → {CONFIG_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
