export interface AmapLngLat {
  lng: number
  lat: number
}

export interface AmapMarkerLabel {
  content: string
  direction: string
  offset: [number, number]
}

export interface AmapMapOptions {
  zoom?: number
  center?: [number, number]
  mapStyle?: string
  viewMode?: string
}

export interface AmapMarkerOptions {
  position: [number, number]
  label?: AmapMarkerLabel
}

export interface AmapPolylineOptions {
  path: Array<[number, number]>
  strokeColor?: string
  strokeWeight?: number
  strokeOpacity?: number
  showDir?: boolean
  lineJoin?: string
}

export interface AmapOverlay {
  setMap(map: AmapMap | null): void
}

export interface AmapMap {
  destroy(): void
  add(overlays: AmapOverlay[]): void
  remove(overlays: AmapOverlay[]): void
  setFitView(overlays?: AmapOverlay[]): void
}

export interface AmapNamespace {
  Map: new (container: HTMLElement, options: AmapMapOptions) => AmapMap
  Marker: new (options: AmapMarkerOptions) => AmapOverlay
  Polyline: new (options: AmapPolylineOptions) => AmapOverlay
}

declare global {
  interface Window {
    AMap?: AmapNamespace
    _AMapSecurityConfig?: { securityJsCode: string }
  }
}

export const PLAN_THEME_COLORS: Record<string, string> = {
  省钱: '#67c23a',
  省时: '#e6a23c',
  均衡: '#409eff',
}