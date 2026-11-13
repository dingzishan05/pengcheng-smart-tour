import type { AmapNamespace } from '@/types/amap'

let loader: Promise<AmapNamespace> | null = null

function injectScript(key: string): void {
  const existing = document.querySelector<HTMLScriptElement>('script[data-amap-loader]')
  if (existing) return
  const securityCode = import.meta.env.VITE_AMAP_SECURITY_CODE ?? ''
  if (securityCode) {
    window._AMapSecurityConfig = { securityJsCode: securityCode }
  }
  const script = document.createElement('script')
  script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(key)}`
  script.async = true
  script.dataset.amapLoader = 'true'
  document.head.appendChild(script)
}

export function loadAmap(): Promise<AmapNamespace> {
  if (loader) return loader
  loader = new Promise<AmapNamespace>((resolve, reject) => {
    const key = import.meta.env.VITE_AMAP_JS_KEY ?? ''
    if (!key) {
      reject(new Error('AMAP_JS_KEY 未配置'))
      return
    }
    injectScript(key)
    const started = Date.now()
    const poll = (): void => {
      if (window.AMap) {
        resolve(window.AMap)
        return
      }
      if (Date.now() - started > 10_000) {
        loader = null
        reject(new Error('地图脚本加载超时'))
        return
      }
      window.setTimeout(poll, 200)
    }
    poll()
  })
  return loader
}