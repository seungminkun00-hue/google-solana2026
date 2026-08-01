"""devnet 지갑 생성 스크립트 (SOL 없어도 실행 가능).

봇 3개 × 지갑 4개 = 12개의 devnet 키페어를 만든다.
지금 만들어두면 SOL이 생기는 즉시 다음 단계로 넘어갈 수 있다.

⚠️ 생성된 wallets/ 폴더는 절대 GitHub에 올리지 말 것.
   (.gitignore에 자동 추가됨)

사용법:
    py -3.13 setup_wallets.py            # 생성
    py -3.13 setup_wallets.py --show     # 주소만 확인
"""
import json
import pathlib
import sys

WALLET_DIR = pathlib.Path("wallets")
ROLES = ["user-treasury", "invest-wallet", "research-agent", "revenue-wallet"]
BOTS = ["bot1", "bot2", "bot3"]

# 봇에 속하지 않는 시스템 지갑.
#   external : 외부 세계 — 사용자 온램프, 외부 에이전트의 구매 대금
#   market   : 스왑 상대방 — 매매 원금 유출입 (AMM 풀 역할)
# mock에서는 가상 이름이었지만 devnet에서는 실제 계정이 필요하다.
SYSTEM_WALLETS = ["external", "market"]


def generate():
    """solders로 키페어 생성. solana-keygen 없이도 동작."""
    try:
        from solders.keypair import Keypair
    except ImportError:
        print("❌ solders가 없습니다. 먼저 설치하세요:")
        print("   py -3.13 -m pip install solders")
        sys.exit(1)

    WALLET_DIR.mkdir(exist_ok=True)
    registry = {}

    targets = [(f"{r}@{b}", f"{r}_{b}") for b in BOTS for r in ROLES]
    targets += [(w, w) for w in SYSTEM_WALLETS]

    for name, fname in targets:
            path = WALLET_DIR / f"{fname}.json"
            if path.exists():
                data = json.loads(path.read_text())
                kp = Keypair.from_bytes(bytes(data))
            else:
                kp = Keypair()
                path.write_text(json.dumps(list(bytes(kp))))
            registry[name] = str(kp.pubkey())

    (WALLET_DIR / "registry.json").write_text(
        json.dumps(registry, indent=2, ensure_ascii=False))

    # 비밀키 유출 방지.
    # provider.yml 은 넣지 않는다 — 비밀이 없고 제출 자산이다.
    # (예전에 여기 있어서 게이트웨이 설계 파일이 제출에서 빠질 뻔했다.)
    gi = pathlib.Path(".gitignore")
    lines = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
    for entry in ["wallets/", "*.keypair.json", "state/"]:
        if entry not in lines:
            lines.append(entry)
    gi.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return registry


def show():
    reg_path = WALLET_DIR / "registry.json"
    if not reg_path.exists():
        print("아직 생성 안 됨. 먼저 인자 없이 실행하세요.")
        return
    reg = json.loads(reg_path.read_text())
    for name, addr in reg.items():
        print(f"  {name:28s} {addr}")


if __name__ == "__main__":
    if "--show" in sys.argv:
        show()
    else:
        reg = generate()
        print(f"✅ 지갑 {len(reg)}개 준비 완료 → wallets/\n")
        for name, addr in reg.items():
            print(f"  {name:28s} {addr}")
        print("\n⚠️ wallets/ 폴더는 .gitignore에 추가됨. 절대 공유하지 말 것.")
        print("\n다음 단계: SOL 확보 후")
        print("  spl-token create-token --decimals 6   # devnet USDC 발행")
