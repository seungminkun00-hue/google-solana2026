"""소유자 전용 관제 — 심사위원이 실제로 들어와서 뭘 했는지 본다.

    GET /owner/activity        머리로 훑기 (JSON)
    GET /owner                 표로 보기 (브라우저)

둘 다 헤더 `x-owner-token` 또는 쿼리 `?t=` 로 인증한다.

[왜 별도 토큰인가]
기존 `ADMIN_TOKEN` 은 프론트 번들에 그대로 들어간다(브라우저가 봇 생성·실행에
쓴다). 즉 공개값이다. 그걸로 이 화면을 지키면 아무나 다른 심사위원의 봇
이름과 거래 내역을 들여다볼 수 있다. 그래서 서버만 아는 값을 따로 쓴다.

`OWNER_TOKEN` 이 비어 있으면 이 라우트는 **아예 등록되지 않는다.** 켜 두지
않은 배포에서 404 가 나는 것이 정상이다. 토큰이 틀렸을 때도 403 이 아니라
404 를 준다 — 403 은 "여기 뭔가 있다" 를 알려주는 셈이라서다.

[세션 값을 그대로 보여주지 않는다]
세션 문자열은 그 브라우저를 사칭할 수 있는 열쇠다. 이 화면에 찍어두면
화면을 캡처해 공유하는 순간 남의 세션이 샌다. 그래서 앞 4자만 남긴다.
누가 누구인지 구분하는 데는 그걸로 충분하다.
"""
from __future__ import annotations

import hashlib
import os
import time

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import HTMLResponse

from app.bots import BOTS
from app.core.journal import JOURNAL
from app.core.positions import BOOK
from app.core.profiles import PROFILES

router = APIRouter(prefix="/owner", tags=["owner"])

BOOT = time.time()


def _guard(token: str | None) -> None:
    want = os.environ.get("OWNER_TOKEN", "").strip()
    # 없는 척한다. 아래 include 조건과 같은 이유.
    if not want or token != want:
        raise HTTPException(404, "Not Found")


def _mask(session: str) -> str:
    """세션을 식별은 되되 사칭은 못 하게.

    앞 몇 글자를 자르는 방식은 못 쓴다 — 브라우저가 만드는 세션 값은
    앞부분이 겹치는 일이 흔해서 서로 다른 사람이 같은 이름으로 보인다.
    해시를 쓰면 길이와 무관하게 갈리고, 되돌릴 수도 없다.
    """
    if not session:
        return "공용"
    return hashlib.sha256(session.encode()).hexdigest()[:6]


def _collect() -> dict:
    now = time.time()
    sessions: dict[str, dict] = {}

    for bot in BOTS.values():
        p = PROFILES.ensure(bot.bot_id)
        calls = JOURNAL.api_calls_of(bot.bot_id)
        fills = JOURNAL.fills_of(bot.bot_id)
        positions = BOOK.of_bot(bot.bot_id)

        # 마지막 흔적 = 결제·체결·생성 중 가장 최근
        last = max([bot.created_at]
                   + [c["ts"] for c in calls]
                   + [f["ts"] for f in fills])

        row = {
            "bot_id": bot.bot_id,
            "name": p.display_name or "(이름 없음)",
            "tagline": p.tagline,
            "markets": p.markets,
            "model": p.model,
            "created_at": bot.created_at,
            "age_min": round((now - bot.created_at) / 60, 1),
            "idle_min": round((now - last) / 60, 1),
            "trades": len(fills),
            "open_positions": len(positions),
            "api_calls": len(calls),
            "spend_micro": sum(c["amount"] for c in calls),
            "killed": bot.killed,
        }

        s = sessions.setdefault(bot.session, {
            "session": _mask(bot.session),
            "bots": [], "first_seen": bot.created_at, "last_seen": last,
        })
        s["bots"].append(row)
        s["first_seen"] = min(s["first_seen"], bot.created_at)
        s["last_seen"] = max(s["last_seen"], last)

    rows = sorted(sessions.values(), key=lambda s: -s["last_seen"])
    for s in rows:
        s["bots"].sort(key=lambda b: -b["created_at"])
        s["idle_min"] = round((now - s["last_seen"]) / 60, 1)

    everyone = [b for s in rows for b in s["bots"]]
    return {
        "now": now,
        "uptime_min": round((now - BOOT) / 60, 1),
        "totals": {
            "sessions": len(rows),
            "bots": len(everyone),
            "trades": sum(b["trades"] for b in everyone),
            "api_calls": sum(b["api_calls"] for b in everyone),
            "spend_micro": sum(b["spend_micro"] for b in everyone),
        },
        "sessions": rows,
    }


@router.get("/activity")
async def activity(x_owner_token: str | None = Header(default=None),
                   t: str | None = Query(default=None)):
    _guard(x_owner_token or t)
    return _collect()


@router.get("", response_class=HTMLResponse)
async def page(x_owner_token: str | None = Header(default=None),
               t: str | None = Query(default=None)):
    """브라우저로 열어보는 표. 5초마다 새로고침한다."""
    _guard(x_owner_token or t)
    d = _collect()
    tt = d["totals"]

    def esc(s: str) -> str:
        return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

    body = []
    if not d["sessions"]:
        body.append('<p class="empty">아직 아무도 봇을 만들지 않았습니다.</p>')
    for s in d["sessions"]:
        body.append(f'<h2>브라우저 <code>{esc(s["session"])}</code> '
                    f'<span class="dim">· 봇 {len(s["bots"])}개 · '
                    f'{s["idle_min"]:.0f}분 전 활동</span></h2>')
        body.append("<table><tr><th>봇 이름</th><th>시장</th><th>만든 지</th>"
                    "<th>거래</th><th>보유</th><th>API 호출</th><th>지출</th>"
                    "<th>마지막</th></tr>")
        for b in s["bots"]:
            body.append(
                f'<tr><td><b>{esc(b["name"])}</b><br>'
                f'<span class="dim">{esc(b["tagline"])}</span></td>'
                f'<td>{esc(", ".join(b["markets"]))}</td>'
                f'<td>{b["age_min"]:.0f}분</td>'
                f'<td>{b["trades"]}</td><td>{b["open_positions"]}</td>'
                f'<td>{b["api_calls"]}</td>'
                f'<td>${b["spend_micro"] / 1e6:.4f}</td>'
                f'<td>{b["idle_min"]:.0f}분 전</td></tr>')
        body.append("</table>")

    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>관제 · Cognitive Economy</title>
<meta http-equiv="refresh" content="5">
<style>
 body{{font:14px/1.6 system-ui,sans-serif;background:#0e1116;color:#e6edf3;
      margin:0;padding:28px 32px}}
 h1{{font-size:20px;margin:0 0 4px}}
 h2{{font-size:15px;margin:26px 0 8px;font-weight:600}}
 .dim{{color:#8b949e;font-weight:400}}
 .cards{{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0 4px}}
 .card{{background:#161b22;border:1px solid #30363d;border-radius:10px;
        padding:10px 16px;min-width:96px}}
 .card b{{display:block;font-size:22px;line-height:1.3}}
 table{{border-collapse:collapse;width:100%;max-width:920px}}
 th,td{{text-align:left;padding:7px 10px;border-bottom:1px solid #21262d;
        vertical-align:top}}
 th{{color:#8b949e;font-weight:500;font-size:12px}}
 code{{background:#21262d;padding:1px 6px;border-radius:4px}}
 .empty{{color:#8b949e;margin:28px 0}}
</style></head><body>
<h1>관제</h1>
<div class="dim">서버 가동 {d["uptime_min"]:.0f}분 · 5초마다 새로고침</div>
<div class="cards">
  <div class="card"><b>{tt["sessions"]}</b>접속 브라우저</div>
  <div class="card"><b>{tt["bots"]}</b>봇</div>
  <div class="card"><b>{tt["trades"]}</b>거래</div>
  <div class="card"><b>{tt["api_calls"]}</b>API 호출</div>
  <div class="card"><b>${tt["spend_micro"] / 1e6:.4f}</b>누적 지출</div>
</div>
{"".join(body)}
</body></html>"""
