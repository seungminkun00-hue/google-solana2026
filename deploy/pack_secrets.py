"""배포 서버로 옮길 비밀 묶음을 만든다.

    py -3.13 deploy/pack_secrets.py

`wallets/` 와 `.env` 는 .gitignore 에 있다. **git 에 올리면 안 된다** —
devnet 개인키 179개와 Gemini·KIS 키가 들어 있다. 그런데 서버에는 있어야
기동한다. 그래서 손으로 옮긴다.

결과: deploy/secrets.tar.gz  (이것도 git 에 올리지 말 것)

[무엇이 들어가나]
  wallets/devnet.json        민트 주소·지갑 목록·수수료 지불자 경로
  wallets/*.json             지갑 키페어 (봇 지갑 포함)
  wallets/market_candidates.json  검증된 종목 목록
  .env                       API 키

[수수료 지불자 경로 주의]
devnet.json 의 `fee_payer_path` 는 이 PC 의 절대경로
(C:\\Users\\...\\.config\\solana\\id.json)다. 리눅스 서버에서는 그 경로가
없으므로 **묶을 때 그 키를 wallets/ 안으로 복사하고 경로를 상대경로로
바꾼다.** 안 그러면 서버에서 "id.json 없음" 으로 기동 실패한다.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import tarfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
WALLETS = ROOT / "wallets"
OUT = ROOT / "deploy" / "secrets.tar.gz"
STAGE = ROOT / "deploy" / "_stage"


def main() -> None:
    cfg_path = WALLETS / "devnet.json"
    if not cfg_path.exists():
        raise SystemExit(f"{cfg_path} 없음 — 이 PC 에서 실행하세요.")

    if STAGE.exists():
        shutil.rmtree(STAGE)
    (STAGE / "wallets").mkdir(parents=True)

    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    # 수수료 지불자 키를 묶음 안으로 들여오고 경로를 상대경로로 바꾼다.
    payer_src = pathlib.Path(cfg["fee_payer_path"])
    if not payer_src.exists():
        raise SystemExit(f"수수료 지불자 키 없음: {payer_src}")
    shutil.copy2(payer_src, STAGE / "wallets" / "fee_payer.json")
    cfg["fee_payer_path"] = "wallets/fee_payer.json"
    (STAGE / "wallets" / "devnet.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")

    # 나머지 지갑 파일. 백업본(.bak·.pre-)은 뺀다 — 서버에서 쓸 일이 없다.
    copied = 0
    for f in WALLETS.glob("*.json"):
        if f.name == "devnet.json" or ".bak" in f.name or ".pre-" in f.name:
            continue
        shutil.copy2(f, STAGE / "wallets" / f.name)
        copied += 1

    env = ROOT / ".env"
    if env.exists():
        shutil.copy2(env, STAGE / ".env")

    OUT.parent.mkdir(exist_ok=True)
    with tarfile.open(OUT, "w:gz") as tar:
        for item in STAGE.iterdir():
            tar.add(item, arcname=item.name)
    shutil.rmtree(STAGE)

    size = OUT.stat().st_size / 1024
    print(f"✅ {OUT}  ({size:,.0f} KB)")
    print(f"   지갑 {copied + 1}개 · .env {'포함' if env.exists() else '없음'}")
    print()
    print("서버에서:")
    print("   tar -xzf secrets.tar.gz -C /app")
    print()
    print("⚠️ 이 파일은 개인키를 담고 있습니다. git·채팅·메일로 보내지 마세요.")


if __name__ == "__main__":
    main()
