"""devnet 실제 SPL 이체 원장.

mock Ledger(딕셔너리 숫자)를 이것으로 교체하면 모든 이체가
실제 온체인 트랜잭션이 된다. Solana Explorer에서 조회 가능.

[핵심 설계 — transfer_many]
정산 분배(85/10/5)는 반드시 **단일 트랜잭션 내 다중 instruction**이어야 한다.
트랜잭션을 3개로 쪼개면 2개만 성공하고 멈추는 사고가 가능하다.
"반쯤 나눠준 상태"가 이 시스템에서 가장 위험한 상태다.

실측 검증(2026-07): transfer_checked 3건 = 379 bytes / 상한 1232 bytes.
                    수취인 10명까지는 단일 트랜잭션으로 안전.

[핵심 설계 — 멱등 전송]  ★ 원자성만으로는 부족하다
단일 트랜잭션은 "반쯤 나눠준 상태"를 막지만 "두 번 나눠준 상태"는
막지 못한다. 재시도가 매번 새 블록해시로 새 트랜잭션을 만들면
시그니처가 달라져 같은 분배가 두 번 집행된다.
_send_tx()가 블록해시를 고정해 재전송을 무해하게 만든다. 상세는 그 함수 주석.

[네트워크 정책]
이 어댑터는 devnet 전용이다. mainnet 주소가 들어오면 예외를 던진다.
실수로 진짜 돈이 나가는 것을 코드 레벨에서 막는다.
"""
from __future__ import annotations

import asyncio
import base64
import json
import pathlib
import time

from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import MessageV0, to_bytes_versioned
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction
from solders.system_program import ID as SYSTEM_PROGRAM_ID
from spl.token.constants import TOKEN_PROGRAM_ID
from spl.token.instructions import (approve_checked,
                                    create_idempotent_associated_token_account,
                                    get_associated_token_address,
                                    transfer_checked)
from spl.token.models import ApproveCheckedParams, TransferCheckedParams

# app.core.ledger 가 아니라 routes 에서 가져온다. ledger.py는 하단에서
# 이 파일을 임포트하므로(_make), 거기서 가져오면 순환이 된다.
from app.core.routes import (ALLOWED_ROLE_ROUTES, InsufficientFunds,
                             RouteViolation, role)
from app.core.proofs import PROOFS
from app.models import PaymentProof

WALLET_DIR = pathlib.Path("wallets")
CONFIG_PATH = WALLET_DIR / "devnet.json"
MAX_LEGS_PER_TX = 8          # 실측 여유 기준 보수적 상한
USDC_DECIMALS = 6
MAX_TX_BYTES = 1_100         # 상한 1232에서 여유를 둔 실사용 상한
# 잔고 캐시 수명. 짧게 잡는다 — 이건 성능 최적화이지 정확성을 양보하는
# 장치가 아니다. 쓰기가 일어나면 TTL과 무관하게 즉시 무효화된다.
SNAPSHOT_TTL = 2.0

# 트랜잭션 확정을 기다리는 간격(초). 폴링 24회분.
#
# [왜 앞을 촘촘하게 잡나 — 2026-08-03 실측]
# 예전에는 `1.0 if poll < 6 else 2.5` 였다. 그런데 sleep 이 **확정 조회보다
# 먼저** 온다. devnet 이 0.3초에 확정해도 1.0초를 자고서야 물어봤다는 뜻이다.
# 사이클 하나에 온체인 트랜잭션이 8~10건이라 그것만으로 8~10초를 버렸다.
# "전체적으로 느리다" 의 가장 큰 몫이 여기였다.
#
# 앞을 0.15초부터 촘촘히 깔고 뒤로 갈수록 늘린다. 흔한 경우(빨리 확정)는
# 훨씬 빨라지고, 드문 경우(RPC 지연)의 총 예산은 그대로 둔다.
#   이전 총합 6×1.0 + 18×2.5 = 51.0초
#   현재 총합                 = 50.7초  — 인내심은 그대로, 대기만 줄었다
#
# 폴링을 앞당기는 것은 이중 집행 위험을 만들지 않는다. 블록해시가 라운드
# 안에서 고정이라 재전송해도 시그니처가 같고, 여기서 하는 일은 '더 일찍
# 물어보는 것' 뿐이다 — 새 트랜잭션을 만드는 곳은 바깥 라운드뿐이다.
_POLL_DELAYS = (
    0.15, 0.25, 0.4, 0.6, 0.9, 1.2,      # 0~5   합 3.5초  (여기서 대부분 끝난다)
    1.5, 1.5, 2.0, 2.0, 2.5, 2.5,        # 6~11  합 12.0초
    2.5, 2.5, 2.5, 2.5, 2.5, 2.5,        # 12~17 합 15.0초
    3.0, 3.0, 3.0, 3.0, 3.5, 3.7,        # 18~23 합 20.2초
)
assert len(_POLL_DELAYS) == 24
# 심사위원 지갑에 채워 줄 devnet SOL. 수수료 한 번이 5,000 lamports 이므로
# 넉넉하지만, '쓰라고' 주는 게 아니라 팬텀의 SOL 부족 경고를 없애려고 준다.
JUDGE_SOL_TOPUP = 5_000_000                  # 0.005 SOL
# fee_payer 가 항상 남겨둬야 할 최소 SOL. 이 밑으로는 아무에게도 안 준다.
SOL_FEE_RESERVE = 20_000_000                 # 0.02 SOL


def _atomic_json(path: pathlib.Path, data) -> None:
    """tmp에 쓰고 교체한다. 쓰는 도중 죽어도 직전 파일이 온전히 남는다.

    지갑 목록이 반쯤 쓰인 채로 남으면 다음 기동에서 원장 전체가
    로드에 실패한다. 지갑 파일은 지우거나 깨뜨리면 복구가 불가능하다.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


class DevnetLedger:
    """mock Ledger와 같은 인터페이스. main.py는 이게 꽂혀 있는지 알 필요가 없다.

    계약은 app/core/ledger.py의 Ledger가 사실상의 기준이다.
    (Protocol 파일 ledger_port.py는 시그니처가 sync라 실제와 맞지 않았고
     아무도 쓰지 않아 2026-08에 삭제했다.)
    """

    def __init__(self, rpc_url: str = "https://api.devnet.solana.com") -> None:
        if "mainnet" in rpc_url:
            raise RuntimeError("이 어댑터는 devnet 전용입니다. mainnet 차단.")
        from solana.rpc.async_api import AsyncClient
        from solana.rpc.commitment import Confirmed
        # [커밋먼트 통일] 기본값 finalized로 두면 조회와 프리플라이트가
        # 10~30초 뒤처진 상태를 본다. 방금 confirmed로 만든 토큰 계정이
        # 아직 '없는 것'으로 보여 다음 이체가 InvalidAccountData로 거부된다.
        # (실측: ATA 8건을 한 트랜잭션으로 묶어 빨라지자 바로 재현됨)
        # 쓰기 확정 기준이 confirmed이므로 읽기도 confirmed로 맞춘다.
        self.client = AsyncClient(rpc_url, commitment=Confirmed)
        self.keypairs: dict[str, Keypair] = {}      # 논리명 → 키페어
        # [외부 소유 지갑 — 2026-08]
        # 심사위원 지갑처럼 **우리가 개인키를 갖지 않는** 지갑. 주소만 안다.
        # 여기 있는 지갑에서 돈을 빼려면 위임(approve)을 받아야 하고,
        # 그 이체는 delegated_transfer() 로만 가능하다. transfer()는
        # keypairs 에서 서명자를 찾으므로 애초에 동작하지 않는다 —
        # 남의 지갑을 우리 마음대로 못 건드린다는 것이 코드로 강제된다.
        self.external_owners: dict[str, Pubkey] = {}
        self.fee_payer: Keypair | None = None       # 수수료 전담 (SOL 보유)
        self.mint: Pubkey | None = None             # devnet USDC 민트
        self.proofs: list[PaymentProof] = []
        self._expected_supply: int | None = None
        # (조회시각, 지갑수, 잔고) — 쓰기가 일어나면 즉시 버린다
        self._snap_cache: tuple[float, int, dict[str, int]] | None = None
        self._load()

    # ── 초기화 ──────────────────────────────────────────────────
    def _load(self) -> None:
        """bootstrap_devnet.py가 만든 지갑과 민트를 읽어온다."""
        if not CONFIG_PATH.exists():
            raise RuntimeError(
                f"{CONFIG_PATH} 없음. 먼저 실행하세요:\n"
                f"  py -3.13 bootstrap_devnet.py")
        cfg = json.loads(CONFIG_PATH.read_text())
        self.mint = Pubkey.from_string(cfg["usdc_mint"])
        self.mirrors = {t: Pubkey.from_string(m)
                        for t, m in cfg.get("mirror_mints", {}).items()}
        # [수수료 설계] 봇 지갑 12개에 각각 SOL을 넣는 건 낭비다.
        # CLI 기본 지갑 하나가 모든 수수료를 대신 낸다.
        # 봇 지갑은 USDC만 보유하고, 이체 승인(서명)만 한다.
        self.fee_payer = Keypair.from_bytes(
            bytes(json.loads(pathlib.Path(cfg["fee_payer_path"]).read_text())))
        for name in cfg["wallets"]:
            # 봇 지갑은 "역할@봇ID" → 파일명 "역할_봇ID.json"
            # 시스템 지갑(external, market)은 @가 없다 → 이름 그대로
            fname = name.replace("@", "_") if "@" in name else name
            path = WALLET_DIR / f"{fname}.json"
            if not path.exists():
                raise RuntimeError(
                    f"{path} 없음. py -3.13 setup_wallets.py 를 먼저 실행하세요.")
            self.keypairs[name] = Keypair.from_bytes(bytes(json.loads(path.read_text())))

    def token_ata(self, wallet: str, ticker: str) -> Pubkey:
        """미러 주식 토큰용 계정 주소."""
        if ticker not in self.mirrors:
            raise ValueError(f"미러 민트 미등록: {ticker}. bootstrap_devnet.py 재실행 필요")
        return get_associated_token_address(self.keypairs[wallet].pubkey(),
                                            self.mirrors[ticker])

    def owner_pubkey(self, wallet: str) -> Pubkey:
        """논리명 → 공개키. 우리 지갑이든 외부 지갑이든 여기로 통일한다."""
        kp = self.keypairs.get(wallet)
        if kp is not None:
            return kp.pubkey()
        pk = self.external_owners.get(wallet)
        if pk is None:
            raise KeyError(f"모르는 지갑: {wallet}")
        return pk

    def ata(self, wallet: str) -> Pubkey:
        """연관 토큰 계정(Associated Token Account) 주소.

        SPL에서 '지갑'은 SOL만 담는다. 토큰은 지갑마다 별도 계정이
        필요하고, 그 주소는 (지갑, 민트)로부터 결정론적으로 계산된다.
        """
        return get_associated_token_address(self.owner_pubkey(wallet), self.mint)

    # ── 단건 이체 ───────────────────────────────────────────────
    async def transfer(self, src: str, dst: str, amount: int,
                       resource: str) -> PaymentProof:
        self._check_route(src, dst)
        ix = self._transfer_ix(src, dst, amount)
        sig = await self._send([ix], payer=src)
        proof = PaymentProof(proof_id=str(sig), payer_wallet=src, payee_wallet=dst,
                             amount=amount, resource=resource)
        self.proofs.append(proof)
        # 페이월이 이 증빙을 검증할 수 있게 등록한다.
        # mock Ledger와 동일한 동작 — 온체인이라고 생략하면
        # "존재하지 않는 결제 증빙"으로 거부당한다.
        PROOFS.register(proof.proof_id, dst, amount, resource)
        return proof

    # ── 다자 원자적 이체 (정산의 심장) ──────────────────────────
    async def transfer_many(self, src: str,
                            legs: list[tuple[str, int, str]]) -> list[PaymentProof]:
        """모든 수취인에게 전부 성공하거나 전부 실패.

        ⚠️ 절대 for 루프로 transfer()를 반복 호출하지 말 것.
           그 순간 원자성이 깨지고 부분 정산 사고가 가능해진다.
        """
        if not legs:
            return []
        if len(legs) > MAX_LEGS_PER_TX:
            raise ValueError(
                f"수취인 {len(legs)}명은 단일 트랜잭션 한도(1232 bytes)를 초과할 수 있습니다. "
                f"{MAX_LEGS_PER_TX}명 이하로 줄이거나 Address Lookup Table을 도입하세요. "
                f"트랜잭션을 쪼개는 것은 원자성을 파괴하므로 금지.")

        for dst, _, _ in legs:
            self._check_route(src, dst)

        total = sum(a for _, a, _ in legs)
        balance = await self.balance(src)
        if balance < total:
            raise InsufficientFunds(f"{src} 정산 재원 부족: {balance} < {total}")

        ixs = [self._transfer_ix(src, dst, amt) for dst, amt, _ in legs]
        sig = await self._send(ixs, payer=src)          # ★ 단일 트랜잭션

        proofs = []
        for dst, amt, res in legs:
            p = PaymentProof(proof_id=f"{sig}#{dst}", payer_wallet=src,
                             payee_wallet=dst, amount=amt, resource=res)
            self.proofs.append(p)
            PROOFS.register(p.proof_id, dst, amt, res)
            proofs.append(p)
        return proofs

    # ── 조회 ────────────────────────────────────────────────────
    async def balance(self, wallet: str) -> int:
        """마이크로 USDC 잔고.

        ⚠️ 설계 원칙: '조회 실패'와 '잔고 0'을 절대 혼동하지 않는다.
        RPC가 429로 거부했는데 0으로 처리하면, 시스템은 "돈이 없다"고
        판단해 정상 거래를 거부하거나 잘못된 결정을 내린다.
        계정이 실제로 없을 때만 0을 반환하고, 그 외에는 예외를 던진다.
        """
        for attempt in range(4):
            try:
                res = await self.client.get_token_account_balance(self.ata(wallet))
                return int(res.value.amount)
            except Exception as e:
                msg = str(e)
                # 계정이 정말 없는 경우 = 진짜 0
                if "could not find account" in msg.lower() or "AccountNotFound" in msg:
                    return 0
                if attempt == 3:
                    raise RuntimeError(f"{wallet} 잔고 조회 실패: {msg[:100]}") from e
                await asyncio.sleep(1.5 * (attempt + 1))
        return 0

    def _invalidate_snapshot(self) -> None:
        """잔고를 바꿨으니 캐시를 버린다. 모든 쓰기 경로가 이걸 부른다."""
        self._snap_cache = None

    async def snapshot(self) -> dict[str, int]:
        """전 지갑 잔고를 단 한 번의 RPC 호출로 가져온다.

        지갑마다 따로 조회하면 46번 호출이 되어 즉시 레이트리밋에 걸린다.
        get_multiple_accounts로 한 번에 받고, SPL 토큰 계정 레이아웃에서
        잔고를 직접 읽는다 (amount는 64번째 바이트부터 8바이트 리틀엔디안).

        [캐시 — 2026-08]
        한 사이클에서 이 함수가 10번 넘게 불린다(잔고 조회·양쪽 만다트·
        자본 청구·대시보드). 그게 레이트리밋의 주범이었다.

        ⚠️ TTL만 걸면 위험하다. 돈을 쓴 직후 캐시가 옛 잔고를 돌려주면
           만다트가 "아직 재원이 있다"고 판단해 같은 돈을 또 승인한다.
           그래서 TTL은 아주 짧게 잡고, **모든 쓰기 경로가 캐시를
           무효화한다**(_send_tx 성공 직후). 캐시가 돌려주는 값은
           '마지막 쓰기 이후'의 상태임이 보장된다.
           지갑 수가 바뀌어도(새 봇 생성) 미스 처리한다.
        """
        # [외부 지갑도 센다 — 2026-08]
        # 심사위원 지갑은 우리 것이 아니지만 잔고 합계에는 들어가야 한다.
        # 빼면 심사위원→user-treasury 예치가 '없던 돈이 생긴 것'으로 보여
        # audit_supply 의 통화량 보존이 깨진다. 세는 것과 서명할 수 있는
        # 것은 별개다 — 이 지갑들은 세기만 하고 우리가 움직이지는 못한다.
        n_wallets = len(self.keypairs) + len(self.external_owners)
        cached = self._snap_cache
        if (cached is not None
                and time.monotonic() - cached[0] < SNAPSHOT_TTL
                and cached[1] == n_wallets):
            return dict(cached[2])

        names = list(self.keypairs) + list(self.external_owners)
        atas = [self.ata(n) for n in names]
        # [2026-08-02] getMultipleAccounts 는 한 번에 100개까지만 받는다
        # ("Too many inputs provided; max 100"). 봇을 만들 때마다 지갑이
        # 4개씩 늘어나므로, 봇 25마리쯤에서 잔고 조회가 통째로 죽는다 —
        # 그 시점부터는 봇 생성도 조회도 전부 500이 된다. 실제로 그렇게 됐다.
        # 100개씩 잘라 부르고 합친다. 지갑이 적으면 예전처럼 호출 1번이다.
        CHUNK = 100
        for attempt in range(4):
            try:
                out = {}
                for i in range(0, len(atas), CHUNK):
                    res = await self.client.get_multiple_accounts(atas[i:i + CHUNK])
                    for name, acc in zip(names[i:i + CHUNK], res.value):
                        if acc is None:
                            out[name] = 0        # 계정 미생성 = 진짜 0
                        else:
                            out[name] = int.from_bytes(acc.data[64:72], "little")
                self._snap_cache = (time.monotonic(), n_wallets, out)
                return dict(out)
            except Exception as e:
                if attempt == 3:
                    raise RuntimeError(f"잔고 일괄 조회 실패: {str(e)[:100]}") from e
                await asyncio.sleep(1.5 * (attempt + 1))
        return {}

    # ── 미러 주식 스왑 ──────────────────────────────────────────
    async def swap_in(self, wallet: str, ticker: str,
                      usdc_amount: int, qty: int) -> PaymentProof:
        """USDC 지불과 토큰 수령을 ★단일 트랜잭션★으로 묶는다.

        이게 원자성의 핵심이다. 두 트랜잭션으로 쪼개면
        "돈은 나갔는데 토큰을 못 받은" 상태가 실제로 발생할 수 있다.
        온체인 스왑이 반드시 원자적이어야 하는 이유.
        """
        self._check_route(wallet, "market")
        ixs = [
            # [2026-08-02] 받는 쪽 토큰 계정을 **이 자리에서** 만든다.
            #
            # 예전에는 봇을 만들 때 전 종목의 계정을 미리 팠다. 미러 토큰이
            # 4종일 때는 괜찮았지만, 시장을 늘려 80종이 되면 봇 하나 만드는 데
            # 계정 80개다 — 임대료도 시간도 그만큼 든다. 실제로 살 종목은
            # 그중 한둘이므로 사는 순간에 만드는 것이 맞다.
            #
            # 멱등 명령이라 이미 있으면 조용히 통과하고, 같은 트랜잭션 안에
            # 있으므로 '계정은 생겼는데 스왑은 실패' 같은 반쪽 상태가 없다.
            create_idempotent_associated_token_account(
                self.fee_payer.pubkey(), self.keypairs[wallet].pubkey(),
                self.mirrors[ticker]),
            self._transfer_ix(wallet, "market", usdc_amount),        # USDC 지불
            self._token_transfer_ix("market", wallet, ticker, qty),  # 토큰 수령
        ]
        # 두 지갑이 각각 자기 자산을 승인해야 하므로 서명자가 둘이다.
        sig = await self._send_multi(ixs, owners=[wallet, "market"])
        proof = PaymentProof(proof_id=str(sig), payer_wallet=wallet,
                             payee_wallet="market", amount=usdc_amount,
                             resource=f"swap-in:{ticker}")
        self.proofs.append(proof)
        return proof

    async def swap_out(self, wallet: str, ticker: str,
                       qty: int, usdc_amount: int) -> PaymentProof:
        """토큰 반납과 USDC 수령을 단일 트랜잭션으로."""
        self._check_route("market", wallet)
        ixs = [
            self._token_transfer_ix(wallet, "market", ticker, qty),
            self._transfer_ix("market", wallet, usdc_amount),
        ]
        sig = await self._send_multi(ixs, owners=[wallet, "market"])
        proof = PaymentProof(proof_id=str(sig), payer_wallet="market",
                             payee_wallet=wallet, amount=usdc_amount,
                             resource=f"swap-out:{ticker}")
        self.proofs.append(proof)
        return proof

    # [삭제됨 2026-08] token_balance — 호출자가 없었고, `except: return 0` 으로
    # 조회 실패를 잔고 0으로 뭉개고 있었다. balance()가 주석까지 달아 지키는
    # 원칙("조회 실패 ≠ 잔고 0")을 바로 아래에서 깨는 지뢰였다.
    # 다시 필요해지면 balance()와 같은 방식으로 새로 쓸 것.

    async def ensure_bot_wallets(self, bot_id: str) -> dict:
        """새로 생긴 봇의 지갑 4개를 즉석에서 만든다.

        bootstrap은 시연용 봇만 준비한다. 사용자가 앱에서 봇을 만들면
        그 시점에 키페어와 토큰 계정을 생성해야 한다.

        [2026-08 수정 — IllegalOwner 사고]
        이전 구현은 계정 하나당 트랜잭션 하나를 페이싱 없이 8건 연속
        전송했다. 공용 devnet RPC가 중간에 429를 던지면 재시도가
        '새' 트랜잭션을 만들어 이미 만들어진 계정을 또 만들려 했고,
        비멱등 Create 명령이 IllegalOwner("Provided owner is not allowed")
        로 거부했다. 에러 문구가 owner를 가리켜 원인 추적이 오래 걸렸는데,
        실제로는 owner와 아무 상관이 없었다.

        수정 두 가지:
          1) create_idempotent_... 로 교체. 계정이 이미 있으면 조용히
             통과하므로 재전송이 안전해진다.
          2) 8건을 한 트랜잭션에 묶는다. RPC 왕복이 8회→1회로 줄어
             레이트리밋을 애초에 건드리지 않는다. 부수 효과로 지갑이
             '전부 생기거나 전부 안 생기거나'가 되어 반쪽 상태가 사라진다.

        존재 여부 사전 조회(get_account_info)도 없앴다. 멱등 명령이
        그 역할을 대신하므로 RPC 8회가 통째로 절약된다.
        """
        created: dict[str, str] = {}
        ixs: list = []

        for wallet_role in ("user-treasury", "invest-wallet",
                            "research-agent", "revenue-wallet"):
            name = f"{wallet_role}@{bot_id}"
            if name in self.keypairs:
                continue
            kp = Keypair()
            self.keypairs[name] = kp
            (WALLET_DIR / f"{wallet_role}_{bot_id}.json").write_text(
                json.dumps(list(bytes(kp))))
            created[name] = str(kp.pubkey())
            # USDC 토큰 계정이 없으면 이체 자체가 불가하다
            ixs.append(create_idempotent_associated_token_account(
                self.fee_payer.pubkey(), kp.pubkey(), self.mint))

        # [2026-08-02] 미러 주식 토큰 계정은 **여기서 만들지 않는다.**
        #
        # 예전에는 invest-wallet 에 전 종목의 계정을 미리 팠다. 4종일 때는
        # 트랜잭션 하나로 끝났지만, 시장을 늘려 80종이 되면 봇 하나 만드는 데
        # 계정 80개다 — 임대료(약 0.16 SOL)도 시간도 그만큼 들고, 실제로 살
        # 종목은 그중 한둘뿐이다.
        # 이제 swap_in 이 매수하는 순간 그 종목 계정만 멱등 생성한다.

        for batch in self._pack(ixs, n_signers=1):
            await self._send_fee_payer_only(batch, label=f"{bot_id} 토큰계정 생성")

        # 온체인 계정이 실제로 만들어진 뒤에 등록한다. 순서가 반대면
        # 계정 생성이 실패했는데 등록만 남는 상태가 생긴다.
        self._register_wallets(created)
        return created

    def _register_wallets(self, new: dict[str, str]) -> None:
        """새 지갑을 devnet.json·registry.json에 등록한다.

        [왜 필요한가]
        이게 없으면 재시작 시 _load()가 이 지갑들을 모른다. 온체인에는
        USDC와 미러 토큰이 그대로 있는데 서명할 키를 원장이 안 들고 있으니
        영원히 못 움직인다 = 자금 좌초. 실제로 주인 없는 봇 지갑이
        wallets/ 에 여러 벌 쌓여 있었다.

        기존 내용은 읽어서 그대로 다시 쓴다 — 절대 덮어쓰지 않는다.
        tmp 파일 원자 교체라 쓰는 중 죽어도 직전 파일이 온전히 남는다.
        """
        if not new:
            return
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            for name in new:
                if name not in cfg["wallets"]:
                    cfg["wallets"].append(name)
            _atomic_json(CONFIG_PATH, cfg)

            reg_path = WALLET_DIR / "registry.json"
            reg = json.loads(reg_path.read_text(encoding="utf-8")) \
                if reg_path.exists() else {}
            reg.update(new)
            _atomic_json(reg_path, reg)
        except Exception as e:                    # noqa: BLE001
            # 등록 실패가 이미 성공한 온체인 거래를 되돌리지는 못한다.
            # 조용히 넘기면 나중에 자금이 사라진 것처럼 보이므로 크게 남긴다.
            print(f"  ⚠️ 지갑 등록 실패 — 재시작 시 {list(new)} 를 잃습니다: "
                  f"{str(e)[:120]}")

    async def audit_supply(self) -> dict:
        """통화량 보존 검증 — devnet 버전.

        mock은 딕셔너리 합계를 봤지만, 온체인에서는 우리가 아는
        지갑들의 실제 잔고 합을 본다. bootstrap 이후로 추가 발행이
        없으므로, 이 합계는 어떤 이체를 해도 변하지 않아야 한다.
        """
        snap = await self.snapshot()
        total = sum(snap.values())
        if self._expected_supply is None:
            self._expected_supply = total       # 첫 관측을 기준으로 삼는다
        return {"expected": self._expected_supply, "actual": total,
                "conserved": total == self._expected_supply}

    # ── 내부 ────────────────────────────────────────────────────
    def _check_route(self, src: str, dst: str) -> None:
        """mock과 동일한 화이트리스트를 온체인에도 그대로 적용.
        사용자 원금이 API 비용으로 새는 경로는 여전히 불가능하다."""
        if (role(src), role(dst)) not in ALLOWED_ROLE_ROUTES:
            raise RouteViolation(f"금지된 경로: {src} → {dst}")

    def _token_transfer_ix(self, src: str, dst: str, ticker: str, qty: int):
        return transfer_checked(TransferCheckedParams(
            program_id=TOKEN_PROGRAM_ID,
            source=self.token_ata(src, ticker),
            mint=self.mirrors[ticker],
            dest=self.token_ata(dst, ticker),
            owner=self.keypairs[src].pubkey(),
            amount=qty,
            decimals=USDC_DECIMALS,
        ))

    async def _send_multi(self, ixs: list, owners: list[str]) -> str:
        """서명자가 여럿인 트랜잭션. 스왑처럼 양쪽이 자산을 내놓을 때 쓴다."""
        signers = [self.fee_payer]
        for o in owners:
            kp = self.keypairs[o]
            if kp.pubkey() not in [s.pubkey() for s in signers]:
                signers.append(kp)
        return await self._send_tx(ixs, signers, label=f"스왑({'+'.join(owners)})")

    async def _send_fee_payer_only(self, ixs: list, label: str = "계정 생성") -> str:
        """수수료 지불자만 서명하는 트랜잭션.

        ATA(토큰 계정) 생성이 여기 해당한다. 계정을 만드는 건
        주인의 승인이 필요 없다 — 누구나 남의 계정을 만들어줄 수 있고,
        비용만 만든 사람이 낸다. 그래서 서명자가 하나뿐이다.
        """
        return await self._send_tx(ixs, [self.fee_payer], label=label)

    def _transfer_ix(self, src: str, dst: str, amount: int):
        return transfer_checked(TransferCheckedParams(
            program_id=TOKEN_PROGRAM_ID,
            source=self.ata(src),
            mint=self.mint,
            dest=self.ata(dst),
            owner=self.keypairs[src].pubkey(),
            amount=amount,
            decimals=USDC_DECIMALS,
        ))

    async def _send(self, ixs: list, payer: str) -> str:
        """수수료는 fee_payer가, 토큰 이체 승인은 owner가 서명한다.

        Solana 트랜잭션은 '수수료 내는 사람'과 '자산 주인'이 달라도 된다.
        덕분에 SOL은 지갑 하나에만 있으면 되고, 봇 지갑 12개는
        USDC만 들고 있으면 된다.
        """
        owner = self.keypairs[payer]
        signers = [self.fee_payer] if owner.pubkey() == self.fee_payer.pubkey() \
                  else [self.fee_payer, owner]
        return await self._send_tx(ixs, signers, label=f"이체({payer})")

    # ── 트랜잭션 전송 계층 ──────────────────────────────────────
    async def _rpc(self, fn, *args, tries: int = 5, **kwargs):
        """조회성 RPC 재시도. 공용 devnet은 429를 자주 던진다.

        ⚠️ send_transaction에는 절대 쓰지 말 것.
           조회는 몇 번을 해도 부작용이 없지만 전송은 아니다.
           이 구분이 무너진 것이 이중 집행 사고의 출발점이었다.
        """
        last = None
        for i in range(tries):
            try:
                return await fn(*args, **kwargs)
            except Exception as e:
                last = e
                await asyncio.sleep(0.8 * (i + 1))
        raise RuntimeError(f"RPC 조회 실패: {str(last)[:150]}") from last

    @staticmethod
    def _already_processed(e: Exception) -> bool:
        """이미 체인에 안착한 트랜잭션을 또 보냈을 때의 응답."""
        m = str(e).lower()
        return "already been processed" in m or "alreadyprocessed" in m

    @staticmethod
    def _preflight_rejected(e: Exception) -> bool:
        """시뮬레이션 단계 거부 = 체인에 안착하지 않았다.
        잔고 부족·계정 없음 등이 여기 해당하고, 재전송해도 결과가 같다."""
        m = str(e).lower()
        return "preflight" in m or "simulation failed" in m

    async def _signature_landed(self, sig) -> bool | None:
        """True=성공 확정, False=온체인 실패 확정, None=아직 모름.

        '모름'을 '실패'로 뭉개지 않는 것이 이 함수의 존재 이유다.
        모름을 실패로 처리하는 순간 이중 집행이 시작된다.
        """
        res = await self._rpc(self.client.get_signature_statuses, [sig],
                              search_transaction_history=True)
        st = res.value[0]
        if st is None:
            return None
        if st.err is not None:
            return False
        # processed 단계는 아직 롤백 가능하므로 확정으로 치지 않는다.
        level = str(st.confirmation_status).rsplit(".", 1)[-1].lower()
        return True if level in ("confirmed", "finalized") else None

    def _tx_bytes(self, ixs: list, n_signers: int) -> int:
        """직렬화 크기 추정. 서명 자리(64바이트 × 서명자)까지 포함한다."""
        msg = MessageV0.try_compile(self.fee_payer.pubkey(), ixs, [], Hash.default())
        return len(bytes(msg)) + 1 + 64 * n_signers

    def _pack(self, ixs: list, n_signers: int = 1) -> list[list]:
        """1232바이트 상한에 맞춰 최소 개수의 트랜잭션으로 묶는다.

        명령을 쪼개면 원자성이 깨지므로, 원자성이 필요한 묶음
        (transfer_many 등)에는 쓰지 않는다. 계정 생성처럼 각각이
        독립적이고 멱등인 작업에만 쓴다.
        """
        out: list[list] = []
        cur: list = []
        for ix in ixs:
            if cur and self._tx_bytes(cur + [ix], n_signers) > MAX_TX_BYTES:
                out.append(cur)
                cur = [ix]
            else:
                cur.append(ix)
        if cur:
            out.append(cur)
        return out

    async def _send_tx(self, ixs: list, signers: list, label: str,
                       rounds: int = 3) -> str:
        """트랜잭션을 '정확히 한 번' 실행한다.

        [왜 이렇게까지 하나 — 2026-08 실측]
        이전 구현은 재시도할 때마다 get_latest_blockhash()를 새로 불렀다.
        블록해시가 바뀌면 메시지가 바뀌고 서명도 바뀌어, 완전히 별개의
        트랜잭션이 된다. 공용 devnet RPC가 confirm 단계에서 429를 던지면
        (자주 그런다) 코드는 '전송 실패'로 오인하고 새 트랜잭션을 또 보냈다.
        실측에서 같은 명령에 서로 다른 시그니처 3개가 만들어졌다.

        ATA 생성은 두 번째부터 IllegalOwner로 막혀 시끄럽게 실패했지만,
        transfer_checked에는 그런 방어가 없다. 정산 분배(85/10/5)가
        통째로 두 번 나가고도 아무 에러가 안 났을 것이다.
        총액은 보존되므로 audit_supply도 통과한다 — 조용히 틀린다.
        단일 트랜잭션(원자성)은 '반쯤 나눠준 상태'를 막을 뿐,
        '두 번 나눠준 상태'는 멱등성이 막는다.

        [설계]
        1. 블록해시를 한 라운드 안에서 고정한다. 재전송해도 시그니처가
           같으므로 런타임이 중복 실행을 거부한다 = 재전송이 무해해진다.
        2. 전송 결과가 불확실하면 새 트랜잭션을 만들지 않는다.
           시그니처 조회로 안착 여부를 먼저 확정한다.
        3. 블록해시가 만료되어 그 트랜잭션이 영원히 안착할 수 없음이
           확실해진 뒤에만 다음 라운드에서 새로 만든다.
        4. 만료 전인데 확정도 안 되면 포기하고 예외를 던진다.
           여기서 새 트랜잭션을 만드는 것이 정확히 이중 집행이다.
           돈 문제는 '조용히 두 번'보다 '시끄럽게 멈춤'이 낫다.
        """
        last_err: Exception | None = None

        for _ in range(rounds):
            bh = await self._rpc(self.client.get_latest_blockhash)
            msg = MessageV0.try_compile(self.fee_payer.pubkey(), ixs, [],
                                        bh.value.blockhash)
            tx = VersionedTransaction(msg, signers)
            sig = tx.signatures[0]
            fatal: Exception | None = None

            for poll in range(24):
                if poll % 6 == 0:
                    # ★ 매번 '같은' tx를 재전송한다. 시그니처가 동일하므로
                    #   이미 안착했다면 런타임이 중복을 거부한다.
                    try:
                        await self.client.send_transaction(tx)
                    except Exception as e:
                        last_err = e
                        if not self._already_processed(e) and self._preflight_rejected(e):
                            # 안착 여부를 한 번 확인한 뒤에 판단한다.
                            # 이미 성공한 tx의 재전송이 잔고 변화 때문에
                            # 시뮬레이션에서 거부될 수 있기 때문.
                            fatal = e

                await asyncio.sleep(_POLL_DELAYS[poll])

                try:
                    landed = await self._signature_landed(sig)
                except RuntimeError:
                    # 조회 자체가 계속 실패해도 여기서 포기하지 않는다.
                    # 조회 실패는 '모름'이지 '실패'가 아니다. 폴링 예산이
                    # 남아 있는 한 계속 물어본다.
                    landed = None
                if landed is True:
                    # 잔고가 바뀌었다. 캐시가 옛 값을 돌려주면 만다트가
                    # 이미 쓴 돈을 또 승인할 수 있다. 모든 쓰기가 여기를
                    # 지나므로 무효화 지점도 여기 하나면 충분하다.
                    self._invalidate_snapshot()
                    return str(sig)
                if landed is False:
                    raise RuntimeError(f"{label} 온체인 실패: {sig}")
                if fatal is not None:
                    raise RuntimeError(f"{label} 거부: {str(fatal)[:200]}") from fatal

            # 폴링 예산 소진. 블록해시가 아직 살아 있으면 이 tx가 나중에
            # 안착할 수 있으므로, 새 tx를 만들지 않고 멈춘다.
            height = (await self._rpc(self.client.get_block_height)).value
            if height <= bh.value.last_valid_block_height:
                raise RuntimeError(
                    f"{label} 확정 실패(미결 상태). 시그니처 {sig} 를 "
                    f"Explorer에서 확인하세요. 이중 집행을 막기 위해 "
                    f"재전송하지 않습니다.")
            # 만료 확정 — 이 tx는 영원히 안착할 수 없다. 이제 새로 만들어도 안전.

        raise RuntimeError(f"{label} 실패: {str(last_err)[:200]}")

    # ── 외부 지갑 (심사위원 시연) ───────────────────────────────
    #
    # [설계 — 왜 '위임'인가]
    # 심사위원 지갑의 개인키는 우리에게 없다. 주소만 안다고 남의 돈을
    # 뺄 수는 없다 — 계좌번호를 안다고 인출이 되지 않는 것과 같다.
    # 그래서 SPL 의 approve(위임)를 쓴다. 심사위원이 **한 번** 서명해
    # "이 봇이 최대 N 까지 꺼내가도 좋다"고 등록하면, 그 뒤로는 봇이
    # 자기 서명만으로 한도 안에서 인출한다. 자동이체 신청서와 같다.
    #
    # 이 구조가 곧 이 프로젝트의 주장이다: 사람의 승인은 위임 시점
    # 한 번뿐이고, 이후의 결제는 에이전트가 단독으로 집행한다.
    async def assert_wallet(self, address: str) -> Pubkey:
        """이 주소가 정말 '지갑' 인지 체인에서 확인한다.

        [왜 필요한가 — 2026-08-02 실측]
        형식 검사만 하면 민트 주소·토큰 계정·프로그램 주소도 전부 통과한다.
        실제로 민트 주소가 등록돼 테스트 USDC 50 이 아무도 못 꺼내는
        계정으로 들어갔고, 위임 트랜잭션은 owner 자리에 민트가 박혀
        팬텀이 'Unexpected error' 로 거절했다. 원인을 짚기까지 오래 걸렸다.

        지갑은 시스템 프로그램이 소유한다. 토큰 관련 계정은 토큰 프로그램이
        소유하므로 여기서 갈린다. 아직 아무것도 없는 새 지갑은 계정 자체가
        없는데, 그건 정상이므로 통과시킨다.
        """
        pk = Pubkey.from_string(address)        # 형식이 틀리면 여기서 죽는다
        info = (await self._rpc(self.client.get_account_info, pk)).value
        if info is None:
            return pk                           # 한 번도 안 쓴 새 지갑
        owner = str(info.owner)
        if owner != str(SYSTEM_PROGRAM_ID):
            hint = ("토큰 민트나 토큰 계정 주소로 보입니다"
                    if owner == str(TOKEN_PROGRAM_ID)
                    else f"프로그램 {owner[:8]}… 이 소유한 계정입니다")
            raise ValueError(f"지갑 주소가 아닙니다 — {hint}. "
                             f"팬텀에 표시되는 '내 지갑 주소' 를 넣으세요.")
        return pk

    def register_external(self, name: str, address: str) -> Pubkey:
        """개인키 없는 외부 지갑을 논리명으로 등록한다.

        ⚠️ 반드시 assert_wallet() 으로 검증한 주소만 넘길 것.
        """
        pk = Pubkey.from_string(address)
        if name in self.keypairs:
            raise ValueError(f"{name} 은 우리가 키를 가진 지갑입니다")
        self.external_owners[name] = pk
        self._invalidate_snapshot()             # 잔고 합계 대상이 늘었다
        return pk

    async def ensure_external_account(self, name: str) -> None:
        """외부 지갑의 USDC 토큰 계정을 만들어 둔다.

        토큰 계정이 없으면 이체도 위임도 불가능하다. 계정 생성은
        주인의 승인이 필요 없고 비용만 우리가 낸다 — 그래서 심사위원은
        SOL 이 한 푼도 없어도 된다.
        """
        ix = create_idempotent_associated_token_account(
            self.fee_payer.pubkey(), self.owner_pubkey(name), self.mint)
        await self._send_fee_payer_only([ix], label=f"{name} 토큰계정")

    async def fund_sol(self, name: str, lamports: int) -> str | None:
        """외부 지갑에 devnet SOL 을 소액 채워 준다. 이미 있으면 안 보낸다.

        [왜 필요한가 — 기능이 아니라 화면 때문이다]
        위임 서명 트랜잭션의 수수료는 fee_payer 가 낸다. 그래서 심사위원은
        SOL 이 0 이어도 서명·전송이 실제로 된다. 그런데 **팬텀은 연결된
        지갑의 SOL 잔고를 보고** "이 거래에 대한 SOL이 충분하지 않습니다"
        라는 빨간 경고를 띄운다. 수수료 지불자가 따로 있다는 걸 팬텀 UI 가
        반영하지 않는다.

        기능은 멀쩡한데 심사위원 눈에는 '실패할 거래' 로 보인다. 시연에서
        그 오해는 치명적이라, 경고가 뜰 이유 자체를 없앤다.

        보내는 양은 수수료 한 번(5,000 lamports)의 1000배다. 이걸로
        무엇을 할 수 있게 하려는 게 아니라 잔고 0 을 면하게 하는 것이 전부다.

        실패해도 예외를 올리지 않는다. SOL 지급은 시연의 곁가지고, 이것
        때문에 지갑 등록 자체가 막히면 본말이 뒤집힌다.
        """
        from solders.system_program import TransferParams
        from solders.system_program import transfer as sol_transfer

        try:
            dest = self.owner_pubkey(name)
            have = (await self._rpc(self.client.get_balance, dest)).value
            if have >= lamports:
                return None                     # 이미 충분하다

            # fee_payer 를 마르게 하지 않는다. 수수료를 못 내면 이 시스템의
            # 모든 온체인 동작이 멈춘다 — SOL 인심보다 그쪽이 훨씬 비싸다.
            ours = (await self._rpc(self.client.get_balance,
                                    self.fee_payer.pubkey())).value
            need = lamports - have
            if ours - need < SOL_FEE_RESERVE:
                print(f"  ⚠️ SOL 지급 건너뜀 — fee_payer 잔고 부족 "
                      f"({ours / 1e9:.4f} SOL)")
                return None

            sig = await self._send_fee_payer_only(
                [sol_transfer(TransferParams(
                    from_pubkey=self.fee_payer.pubkey(),
                    to_pubkey=dest,
                    lamports=need))],
                label=f"{name} SOL 지급")
            print(f"  ◎ {name} 에 devnet SOL {need / 1e9:.4f} 지급")
            return sig
        except Exception as e:                            # noqa: BLE001
            print(f"  ⚠️ SOL 지급 실패(무시): {str(e)[:90]}")
            return None

    async def fund_external(self, name: str, amount: int) -> PaymentProof:
        """시연용 테스트 USDC 지급.

        새로 발행(mint)하지 않고 external 지갑에서 **이체**한다.
        발행하면 통화량이 늘어 audit_supply 의 보존 검증이 깨진다.
        시연용 코인도 어딘가에서 와야 한다는 규칙을 예외 없이 지킨다.
        """
        await self.ensure_external_account(name)
        return await self.transfer("external", name, amount, "judge-faucet")

    async def build_approve_tx(self, owner_name: str, delegate_name: str,
                               amount: int) -> str:
        """심사위원이 서명할 위임 트랜잭션을 만들어 base64 로 돌려준다.

        수수료는 우리 fee_payer 가 낸다. 그래서 서명자가 둘이고,
        우리 몫은 여기서 미리 채운 뒤 심사위원 자리만 비워 보낸다
        (부분 서명). 브라우저의 팬텀이 그 자리를 채워 전송한다.

        심사위원에게 SOL 을 요구하지 않기 위한 구조다. devnet SOL 을
        받아오라고 하는 순간 시연이 거기서 막힌다.
        """
        await self.ensure_external_account(owner_name)
        ix = approve_checked(ApproveCheckedParams(
            program_id=TOKEN_PROGRAM_ID,
            source=self.ata(owner_name),
            mint=self.mint,
            delegate=self.owner_pubkey(delegate_name),
            owner=self.owner_pubkey(owner_name),
            amount=amount,
            decimals=USDC_DECIMALS,
            signers=[],
        ))
        bh = await self._rpc(self.client.get_latest_blockhash)
        msg = MessageV0.try_compile(self.fee_payer.pubkey(), [ix], [],
                                    bh.value.blockhash)
        # 서명 순서는 메시지가 요구하는 서명자 순서와 같아야 한다.
        # fee_payer 가 0번(수수료 지불자), 심사위원이 1번이다.
        our_sig = self.fee_payer.sign_message(to_bytes_versioned(msg))
        tx = VersionedTransaction.populate(msg, [our_sig, Signature.default()])
        return base64.b64encode(bytes(tx)).decode()

    async def delegate_status(self, name: str) -> dict:
        """이 지갑이 누구에게 얼마를 위임해 뒀는지 온체인에서 직접 읽는다.

        SPL 토큰 계정 레이아웃(165바이트):
          amount 64..72 · delegate COption<Pubkey> 72..108
          · delegated_amount 121..129
        COption 은 앞 4바이트가 1이면 값이 있다는 뜻이다.
        """
        res = await self._rpc(self.client.get_account_info, self.ata(name))
        acc = res.value
        if acc is None:
            return {"exists": False, "balance": 0,
                    "delegate": None, "allowance": 0}
        d = acc.data
        has_delegate = int.from_bytes(d[72:76], "little") == 1
        return {
            "exists": True,
            "balance": int.from_bytes(d[64:72], "little"),
            "delegate": str(Pubkey.from_bytes(d[76:108])) if has_delegate else None,
            "allowance": int.from_bytes(d[121:129], "little") if has_delegate else 0,
        }

    async def delegated_transfer(self, src: str, dst: str, amount: int,
                                 resource: str) -> PaymentProof:
        """위임받은 권한으로 외부 지갑에서 인출한다.

        서명자가 지갑 주인이 아니라 **위임받은 쪽**이라는 점만
        transfer() 와 다르다. 심사위원의 추가 서명은 필요 없다 —
        이 함수가 도는 순간이 곧 '사람 개입 없는 결제'다.

        한도를 넘으면 SPL 런타임이 거부한다. 우리가 검사해서가 아니라
        체인이 막는다는 점이 중요하다. 위임은 백지수표가 아니다.
        """
        self._check_route(src, dst)
        delegate = self.keypairs.get(dst)
        if delegate is None:
            raise ValueError(f"{dst} 는 우리가 서명할 수 있는 지갑이 아닙니다")

        ix = transfer_checked(TransferCheckedParams(
            program_id=TOKEN_PROGRAM_ID,
            source=self.ata(src),
            mint=self.mint,
            dest=self.ata(dst),
            owner=delegate.pubkey(),        # ★ 주인이 아니라 위임받은 쪽
            amount=amount,
            decimals=USDC_DECIMALS,
        ))
        sig = await self._send_tx([ix], [self.fee_payer, delegate],
                                  label=f"위임인출({src}→{dst})")
        proof = PaymentProof(proof_id=str(sig), payer_wallet=src,
                             payee_wallet=dst, amount=amount, resource=resource)
        self.proofs.append(proof)
        PROOFS.register(proof.proof_id, dst, amount, resource)
        return proof

    async def close(self) -> None:
        await self.client.close()
