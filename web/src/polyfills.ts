/**
 * 브라우저에 없는 Node 전역을 채운다.
 *
 * `@solana/web3.js` 는 트랜잭션을 다룰 때 Node 의 `Buffer` 를 쓴다.
 * 브라우저에는 없어서 `VersionedTransaction.deserialize()` 가
 * "Cannot access buffer.Buffer" 로 터지는데, 그 예외가 지갑 연동
 * 안쪽에서 잡혀 화면에는 그냥 'Unexpected error' 로만 보였다.
 * 원인을 짚기까지 오래 걸린 종류의 문제다.
 *
 * ⚠️ 별도 파일인 이유: import 문은 호이스팅된다. main.tsx 안에
 *    코드로 써두면 App.tsx 가 **먼저** 평가되므로 늦을 수 있다.
 *    모듈은 import 순서대로 평가되므로, 이 파일을 맨 위에서
 *    불러오면 확실히 먼저 실행된다.
 */
import { Buffer } from 'buffer'

declare global {
  // eslint-disable-next-line no-var
  var Buffer: typeof import('buffer').Buffer
}

if (!globalThis.Buffer) globalThis.Buffer = Buffer
