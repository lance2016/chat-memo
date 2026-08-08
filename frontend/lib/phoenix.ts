/** Phoenix 界面地址。
 *
 * **不再需要任何配置。** 后端只知道 compose 网络里的 `phoenix:6006`，浏览器要的是
 * 宿主机映射端口，两个地址不通用 —— 原来靠构建期的 `NEXT_PUBLIC_PHOENIX_URL` 传，
 * 于是又多一个环境变量，还得在构建时就定死。
 *
 * 改成在浏览器里按当前访问的主机名拼：本机访问天然对；
 * 局域网访问拼出来连不上，但那本来就是 compose 把端口绑在 `127.0.0.1` 的结果，
 * 不是这里能解决的问题。
 */
import { useEffect, useState } from "react";

export const PHOENIX_PORT = 16006;

function currentPhoenixUrl(): string {
  if (typeof window === "undefined") return "";
  const { protocol, hostname } = window.location;
  return `${protocol}//${hostname}:${PHOENIX_PORT}`;
}

/** 在 effect 里取，服务端渲染时是空串。
 *
 * 直接在渲染期读 `window.location` 会让 SSR 输出空 href、客户端输出真地址，
 * React 会报水合不一致。放进 effect 后服务端干脆不渲染这个链接。
 */
export function usePhoenixUrl(): string {
  const [url, setUrl] = useState("");
  useEffect(() => setUrl(currentPhoenixUrl()), []);
  return url;
}
