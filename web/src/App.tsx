import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { ActivityProvider } from './components/ActivityPanel'
import { DeviceFrame } from './components/DeviceFrame'
import { FillToasts } from './components/FillToasts'
import { IntroBoot } from './components/IntroBoot'
import { Sidebar } from './components/Sidebar'
import { TourProvider } from './components/TourContext'
import { Home } from './screens/Home'
import { BotDetail } from './screens/BotDetail'
import { BotChat } from './screens/BotChat'
import { BotSettings } from './screens/BotSettings'

export default function App() {
  return (
    <BrowserRouter>
      {/* 안내 패널이 '지금 어느 화면인지' 를 알아야 하므로 라우터 안쪽이다.
          실행 로그는 앱(목업 안)과 지갑 시연(목업 밖)이 함께 쓰므로
          둘을 모두 감싸는 자리에 둔다. */}
      <ActivityProvider>
      <TourProvider>
        {/* 심사용 링크는 데스크톱에서 열린다. 아이폰 목업 안에 넣어야
            '앱'으로 읽힌다 — 좁은 화면에서는 목업 없이 꽉 채운다.
            오른쪽 패널은 목업 바깥이다. 앱이 하는 일이 아니라 심사위원이
            직접 확인하거나 안내를 읽는 자리이기 때문이다. */}
        <DeviceFrame aside={<Sidebar />}>
          {/* 홈 화면 → SOL 스플래시 → 온보딩. 실제 앱을 켜는 흐름 그대로. */}
          <IntroBoot>
            {/* 체결 알림. IntroBoot 안쪽이라 앱에 들어온 뒤에만 뜬다 —
                스플래시 위에 알림이 떠 있으면 앱을 켜기도 전에 알림을
                받은 셈이 된다. */}
            <FillToasts />
            <Routes>
              <Route path="/" element={<Home />} />
              {/* 봇 생성은 수정과 같은 화면을 쓴다 — /bot/:id 보다 먼저 잡아야
                  'new' 가 봇 아이디로 해석되지 않는다. */}
              <Route path="/bot/new" element={<BotSettings mode="create" />} />
              <Route path="/bot/:id" element={<BotDetail tab="summary" />} />
              <Route path="/bot/:id/trades" element={<BotDetail tab="trades" />} />
              <Route path="/bot/:id/apis" element={<BotDetail tab="apis" />} />
              <Route path="/bot/:id/chat" element={<BotChat />} />
              <Route
                path="/bot/:id/settings"
                element={<BotSettings mode="edit" />}
              />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </IntroBoot>
        </DeviceFrame>
      </TourProvider>
      </ActivityProvider>
    </BrowserRouter>
  )
}
