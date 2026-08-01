"""devnet 초기 설정 — SOL이 생기면 이 스크립트 하나만 실행하면 된다.

하는 일:
  1. devnet USDC 민트 발행 (우리 세계의 화폐)
  2. 미러 주식 토큰 4종 발행 (NVDAx/TSLAx/MSFTx/AAPLx)
  3. 데모봇 지갑 + 시스템 지갑에 ATA(토큰 계정) 생성
  4. 각 지갑을 목표 잔고까지 채운다 (부족분만)
  5. wallets/devnet.json 에 결과 저장

[재실행 안전 — 2026-08]
이 스크립트는 여러 번 돌리는 것을 전제로 한다. 그래서 세 가지를 지킨다.
  · 계정 생성은 멱등 명령을 쓴다 (이미 있으면 조용히 통과)
  · 발행은 '목표까지 부족분만'. 예전에는 실행할 때마다 목표액을 통째로
    또 발행해서, 돌릴수록 통화량이 늘었다.
  · 잔고를 '모를' 때는 발행하지 않는다. 조회 실패를 0으로 뭉개면
    이미 채워진 지갑에 또 발행하게 된다.
시드 대상은 데모봇(bot1~3)과 시스템 지갑뿐이다. 사용자가 앱에서 만든
봇의 지갑은 registry에 있어도 건드리지 않는다 — 그쪽은 POST /bots 가
external에서 예치한다.

사전 준비:
  py -3.13 setup_wallets.py          # 지갑 12개 생성
  solana config set --url https://api.devnet.solana.com
  solana airdrop 0.5                 # 수수료용 SOL

실행:
  py -3.13 bootstrap_devnet.py
  py -3.13 bootstrap_devnet.py --check   # 상태만 확인
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys

WALLET_DIR = pathlib.Path("wallets")
CONFIG_PATH = WALLET_DIR / "devnet.json"
RPC = "https://api.devnet.solana.com"

USDC_DECIMALS = 6
INITIAL_USDC = {          # 지갑별 목표 잔고 (마이크로 USDC)
    "user-treasury": 500_000_000,   # $500
    "invest-wallet": 20_000_000,    # $20
    "research-agent": 0,            # 0에서 시작 — 반드시 인보이스로 받는다
    "revenue-wallet": 150_000,      # $0.15 시드
    # 시스템 지갑: 외부 세계와 스왑 상대방. 넉넉히 채워둔다.
    "external": 100_000_000_000,    # $100,000
    "market": 100_000_000_000,      # $100,000
}
MIRROR_TICKERS = ["NVDAx", "TSLAx", "MSFTx", "AAPLx"]
MARKET_TOKEN_TARGET = 1_000_000_000     # market이 들고 있을 미러 토큰 목표
# 재고가 이 아래로 떨어졌을 때만 목표까지 채운다.
#
# 목표에 정확히 맞추려 하면 안 된다. 봇이 포지션을 열고 있는 동안
# market 잔고는 그만큼 비어 있는 게 정상인데, 그걸 매번 다시 채우면
# 봇이 청산할 때 물량이 돌아와 market이 목표를 넘고, 재실행할수록
# 미러 토큰 총발행량이 드리프트한다.
# (실측: 재실행 1회에 NVDAx +0.398760, MSFTx +0.132679, AAPLx +0.100407)
# 바닥선을 두면 정상 매매로 생기는 변동은 무시하고, 재고가 실제로
# 말랐을 때만 보충한다.
MARKET_TOKEN_FLOOR = 200_000_000        # 목표의 20%

# 이 스크립트가 시드하는 대상. registry.json에는 사용자가 앱에서 만든
# 봇의 지갑도 들어 있는데, 그건 여기서 돈을 넣어줄 대상이 아니다.
# 사용자 봇은 POST /bots 가 external에서 예치받는다.
# (실제로 고아 지갑 36개가 registry에 등록돼 있었고, 그대로 두면
#  재실행할 때마다 그 지갑들에 $500씩 새로 발행됐다.)
SEED_BOTS = ["bot1", "bot2", "bot3"]
SEED_SYSTEM = ["external", "market"]


def is_seed_target(name: str) -> bool:
    if "@" not in name:
        return name in SEED_SYSTEM
    return name.split("@", 1)[1] in SEED_BOTS


def resolve_cli_keypair() -> pathlib.Path:
    """solana CLI가 실제로 쓰는 키페어 경로를 알아낸다.

    `solana config set --keypair ...` 로 바꿨을 경우 id.json이 아닐 수 있다.
    에어드랍은 이 지갑으로 들어오므로 반드시 일치해야 한다.
    """
    cfg = pathlib.Path.home() / ".config" / "solana" / "cli" / "config.yml"
    if cfg.exists():
        for line in cfg.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("keypair_path:"):
                return pathlib.Path(line.split(":", 1)[1].strip())
    return pathlib.Path.home() / ".config" / "solana" / "id.json"


def need(msg: str) -> None:
    print(f"❌ {msg}")
    sys.exit(1)


async def main(check_only: bool = False) -> None:
    try:
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
    except ImportError as e:
        need(f"부품 부족: {e}\n   py -3.13 -m pip install -r requirements.txt")

    reg_path = WALLET_DIR / "registry.json"
    if not reg_path.exists():
        need("지갑이 없습니다.  py -3.13 setup_wallets.py  를 먼저 실행하세요.")
    registry = json.loads(reg_path.read_text())

    # 수수료를 낼 주 지갑 = solana CLI가 현재 쓰는 키페어.
    # `solana config set --keypair`로 바꿨을 수 있으므로 하드코딩하지 않고
    # 설정 파일에서 실제 경로를 읽는다. (에어드랍 받은 그 지갑이어야 함)
    default_kp_path = resolve_cli_keypair()
    if not default_kp_path.exists():
        need(f"{default_kp_path} 없음.  solana-keygen new  를 먼저 실행하세요.")
    payer = Keypair.from_bytes(bytes(json.loads(default_kp_path.read_text())))

    client = AsyncClient(RPC)
    print(f"지불 지갑: {payer.pubkey()}")

    lamports = (await client.get_balance(payer.pubkey())).value
    sol = lamports / 1e9
    print(f"SOL 잔고 : {sol:.4f}")
    if sol < 0.05:
        await client.close()
        need("SOL 부족 (최소 0.05 필요).  solana airdrop 0.5  를 먼저 실행하세요.")

    if check_only:
        print("✅ 사전 조건 충족. --check 없이 다시 실행하면 설정을 진행합니다.")
        await client.close()
        return

    async def send(ixs, signers, retries: int = 6):
        """실패하면 점점 더 오래 기다렸다 재시도한다.

        공용 devnet RPC는 초당 요청 제한이 있고, 붐빌 때는
        blockhash 만료·시뮬레이션 실패도 자주 난다. 대부분 재시도하면 된다.
        """
        last = None
        for attempt in range(retries):
            try:
                bh = (await client.get_latest_blockhash()).value.blockhash
                msg = MessageV0.try_compile(payer.pubkey(), ixs, [], bh)
                tx = VersionedTransaction(msg, signers)
                sig = (await client.send_transaction(tx)).value
                await client.confirm_transaction(sig, commitment=Confirmed)
                await asyncio.sleep(1.2)          # 다음 요청까지 여유
                return sig
            except Exception as e:
                last = e
                if attempt == retries - 1:
                    break
                wait = 4 * (attempt + 1)          # 4 → 8 → 12 ...
                print(f"     ⏳ 재시도 {attempt+1}/{retries} ({wait}s): {str(e)[:70]}")
                await asyncio.sleep(wait)
        raise RuntimeError(f"{retries}회 실패: {last}")

    async def create_mint(label: str) -> Pubkey:
        """새 토큰 종류를 발행한다. 민트 = 화폐의 종류."""
        mint_kp = Keypair()
        rent = (await client.get_minimum_balance_for_rent_exemption(82)).value
        ixs = [
            create_account(CreateAccountParams(
                from_pubkey=payer.pubkey(), to_pubkey=mint_kp.pubkey(),
                lamports=rent, space=82, owner=TOKEN_PROGRAM_ID)),
            initialize_mint(InitializeMintParams(
                program_id=TOKEN_PROGRAM_ID, mint=mint_kp.pubkey(),
                decimals=USDC_DECIMALS, mint_authority=payer.pubkey())),
        ]
        await send(ixs, [payer, mint_kp])
        print(f"  {label:8s} {mint_kp.pubkey()}")
        return mint_kp.pubkey()

    print("\n① 토큰 발행")
    # 이미 발행했으면 재사용한다. 재실행할 때마다 새 민트를 만들면
    # 앞서 배분한 잔고가 전부 무의미해진다.
    if CONFIG_PATH.exists():
        prev = json.loads(CONFIG_PATH.read_text())
        usdc_mint = Pubkey.from_string(prev["usdc_mint"])
        mirrors = prev["mirror_mints"]
        print(f"  기존 민트 재사용 (새로 만들려면 {CONFIG_PATH} 삭제)")
        print(f"  USDC     {usdc_mint}")
    else:
        usdc_mint = await create_mint("USDC")
        mirrors = {t: str(await create_mint(t)) for t in MIRROR_TICKERS}

    async def read_balance(ata) -> int | None:
        """토큰 잔고. 계정이 없으면 0, **모르면 None**.

        ⚠️ 조회 실패를 0으로 뭉개면 안 된다. 그러면 이미 채워진 지갑에
           또 발행해서 통화량이 조용히 부풀어 오른다. 이 스크립트는
           재실행을 전제로 하므로 특히 위험하다.
        """
        for i in range(5):
            try:
                r = await client.get_token_account_balance(ata)
                return int(r.value.amount)
            except Exception as e:
                m = str(e).lower()
                if "could not find account" in m or "accountnotfound" in m:
                    return 0
                await asyncio.sleep(1.0 * (i + 1))
        return None

    targets = {n: a for n, a in registry.items() if is_seed_target(n)}
    skipped = len(registry) - len(targets)
    print(f"\n② 지갑 {len(targets)}개 토큰 계정 생성 + 초기 분배")
    if skipped:
        print(f"   (시드 대상 아님으로 {skipped}개 제외 — 사용자 생성 봇의 "
              f"지갑은 앱이 external에서 예치한다)")
    ok, fail = 0, 0
    for name, addr in targets.items():
        owner = Pubkey.from_string(addr)
        role = name.split("@")[0]
        target = INITIAL_USDC.get(role, 0)
        ata = get_associated_token_address(owner, usdc_mint)
        try:
            # 멱등 생성. 이미 있으면 조용히 통과하므로 존재 조회가 필요 없다.
            # (예전에는 조회 실패를 '없음'으로 처리해 비멱등 Create를 다시
            #  보냈고, 그게 IllegalOwner의 원인이었다 — devnet_ledger와 같은 뿌리)
            await send([create_idempotent_associated_token_account(
                payer.pubkey(), owner, usdc_mint)], [payer])

            # 목표 잔고까지 '부족분만' 발행한다. 예전에는 재실행할 때마다
            # 목표액을 통째로 또 발행해서, 돌릴수록 통화량이 늘었다.
            minted = 0
            if target:
                cur = await read_balance(ata)
                if cur is None:
                    print(f"  {name:28s} ⚠️ 잔고 조회 실패 — 발행 건너뜀"
                          f" (모르는 채로 발행하지 않는다)")
                elif cur < target:
                    minted = target - cur
                    await send([mint_to(MintToParams(
                        program_id=TOKEN_PROGRAM_ID, mint=usdc_mint, dest=ata,
                        mint_authority=payer.pubkey(), amount=minted))], [payer])

            print(f"  {name:28s} 목표 ${target/1e6:>10.2f}  "
                  f"발행 ${minted/1e6:>10.2f}  ✅")
            ok += 1
        except Exception as e:
            print(f"  {name:28s} ❌ {str(e)[:110]}")
            fail += 1

    print(f"\n  성공 {ok} / 실패 {fail}")
    if fail:
        print("  ⚠️ 실패분이 있습니다. 스크립트를 한 번 더 실행하면")
        print("     이미 채워진 지갑은 건너뛰고 실패분만 재시도합니다.")

    # ── ③ 미러 주식 토큰 계정 + market에 공급물량 ────────────────
    # market이 팔 물량을 들고 있어야 스왑이 성립한다.
    # 각 invest-wallet도 받을 계정이 미리 있어야 한다.
    print("\n③ 미러 주식 토큰 계정")
    holders = [n for n in targets
               if n.startswith("invest-wallet@")] + ["market"]
    for ticker, mint_str in mirrors.items():
        mint = Pubkey.from_string(mint_str)
        for name in holders:
            owner = Pubkey.from_string(registry[name])
            ata = get_associated_token_address(owner, mint)
            try:
                await send([create_idempotent_associated_token_account(
                    payer.pubkey(), owner, mint)], [payer])
                note = ""
                # market에는 팔 물량을 채워둔다 — 부족분만.
                if name == "market":
                    bal = await read_balance(ata)
                    if bal is None:
                        # 예전에는 여기서 조회에 실패하면 bal=0으로 두고
                        # 10억 개를 또 발행했다. 재실행할수록 미러 토큰
                        # 공급량이 불어난다.
                        note = "⚠️ 잔고 조회 실패 — 발행 건너뜀"
                    elif bal < MARKET_TOKEN_FLOOR:
                        add = MARKET_TOKEN_TARGET - bal
                        await send([mint_to(MintToParams(
                            program_id=TOKEN_PROGRAM_ID, mint=mint, dest=ata,
                            mint_authority=payer.pubkey(),
                            amount=add))], [payer])
                        note = f"재고 바닥 — {add/1e6:,.4f} 발행"
                    else:
                        note = f"재고 {bal/1e6:,.4f} — 발행 없음"
                print(f"  {ticker:6s} {name:22s} ✅ {note}")
            except Exception as e:
                print(f"  {ticker:6s} {name:22s} ❌ {str(e)[:70]}")

    CONFIG_PATH.write_text(json.dumps({
        "rpc": RPC,
        "usdc_mint": str(usdc_mint),
        "mirror_mints": mirrors,
        "payer": str(payer.pubkey()),
        "fee_payer_path": str(default_kp_path),
        "wallets": list(registry),
    }, indent=2))

    print(f"\n✅ 완료 → {CONFIG_PATH}")
    print("\n다음 단계")
    print("  1. app/main.py 에서 LEDGER 를 DevnetLedger 로 교체")
    print("  2. py -3.13 test_cycle.py   ← 이제 진짜 온체인 트랜잭션")
    print(f"  3. Explorer 확인: https://explorer.solana.com/address/{usdc_mint}?cluster=devnet")

    await client.close()


async def verify() -> None:
    """온체인 실제 잔고를 조회해 설정이 제대로 됐는지 확인한다."""
    from solana.rpc.async_api import AsyncClient
    from solders.pubkey import Pubkey
    from spl.token.instructions import get_associated_token_address

    if not CONFIG_PATH.exists():
        need("devnet.json 없음. bootstrap_devnet.py 를 먼저 실행하세요.")
    cfg = json.loads(CONFIG_PATH.read_text())
    reg = json.loads((WALLET_DIR / "registry.json").read_text())
    mint = Pubkey.from_string(cfg["usdc_mint"])
    client = AsyncClient(cfg["rpc"])

    print(f"USDC 민트: {mint}\n")
    total = 0
    for name, addr in reg.items():
        ata = get_associated_token_address(Pubkey.from_string(addr), mint)
        try:
            bal = int((await client.get_token_account_balance(ata)).value.amount)
            total += bal
            print(f"  {name:28s} ${bal/1e6:>10.2f}  ✅")
        except Exception:
            print(f"  {name:28s} {'계정 없음':>14s}  ❌")
        await asyncio.sleep(0.4)
    print(f"\n  총 발행량: ${total/1e6:,.2f}")

    # 미러 주식 토큰 계정도 확인한다. 스왑이 되려면 이게 있어야 한다.
    mirrors = cfg.get("mirror_mints", {})
    if mirrors:
        print("\n미러 주식 토큰 계정")
        holders = [n for n in reg if n.startswith("invest-wallet@")] + ["market"]
        for ticker, mstr in mirrors.items():
            m = Pubkey.from_string(mstr)
            line = []
            for name in holders:
                ata = get_associated_token_address(Pubkey.from_string(reg[name]), m)
                try:
                    b = int((await client.get_token_account_balance(ata)).value.amount)
                    line.append(f"{name.split('@')[-1]}:{b/1e6:.4f}")
                except Exception:
                    line.append(f"{name.split('@')[-1]}:없음❌")
                await asyncio.sleep(0.3)
            print(f"  {ticker:6s} " + "  ".join(line))

    await client.close()


if __name__ == "__main__":
    if "--verify" in sys.argv:
        asyncio.run(verify())
    else:
        asyncio.run(main(check_only="--check" in sys.argv))
