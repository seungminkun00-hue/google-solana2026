/**
 * 팬텀 지갑 최소 연동.
 *
 * @solana/wallet-adapter 를 쓰지 않는다. 우리가 필요한 건 '연결'과
 * '이미 만들어진 트랜잭션에 서명해서 보내기' 둘뿐인데, 어댑터는 그걸
 * 위해 리액트 컨텍스트·모달·다중 지갑 목록을 함께 들여온다.
 * 시연 화면 하나 때문에 앱 전체의 의존성을 늘릴 이유가 없다.
 */
import { VersionedTransaction } from '@solana/web3.js'

type PhantomProvider = {
  isPhantom?: boolean
  publicKey?: { toString(): string } | null
  connect(): Promise<{ publicKey: { toString(): string } }>
  disconnect(): Promise<void>
  signAndSendTransaction(
    tx: VersionedTransaction,
  ): Promise<{ signature: string }>
}

declare global {
  interface Window {
    phantom?: { solana?: PhantomProvider }
    solana?: PhantomProvider
  }
}

/**
 * 지갑 공급자를 찾는다.
 *
 * `isPhantom` 플래그로 거르지 않는다. 그 플래그는 팬텀이 붙이는 표식일
 * 뿐이고, 솔플레어 등 다른 지갑은 안 붙이면서도 같은 인터페이스를 낸다.
 * 표식을 요구하면 멀쩡히 깔린 지갑을 '없음' 으로 판정한다.
 * 우리가 실제로 쓰는 두 메서드가 있는지만 본다 — 그게 진짜 조건이다.
 */
export function getProvider(): PhantomProvider | null {
  const p = window.phantom?.solana ?? window.solana
  if (!p) return null
  const usable =
    typeof p.connect === 'function' &&
    typeof p.signAndSendTransaction === 'function'
  return usable ? p : null
}

export const PHANTOM_INSTALL_URL = 'https://phantom.app/download'

export async function connect(): Promise<string> {
  const p = getProvider()
  if (!p) throw new Error('팬텀 지갑이 없습니다')
  const { publicKey } = await p.connect()
  return publicKey.toString()
}

/**
 * 서버가 부분 서명해 보낸 트랜잭션에 심사위원 서명을 얹어 전송한다.
 *
 * 수수료 지불자는 서버 쪽 지갑이라 심사위원은 devnet SOL 이 없어도 된다.
 * 다만 **팬텀이 devnet 에 있어야** 한다 — mainnet 에 있으면 우리가 넣은
 * 블록해시가 그 체인에 없어서 거부된다. 에러 문구가 원인을 짚어주지
 * 않으므로 호출부에서 안내한다.
 */
export async function signAndSend(txBase64: string): Promise<string> {
  const p = getProvider()
  if (!p) throw new Error('팬텀 지갑이 없습니다')

  const raw = Uint8Array.from(atob(txBase64), (c) => c.charCodeAt(0))
  const tx = VersionedTransaction.deserialize(raw)
  const { signature } = await p.signAndSendTransaction(tx)
  return signature
}
