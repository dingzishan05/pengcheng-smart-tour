export interface GeoPoint {
  lng: number
  lat: number
}

export function locateMe(timeoutMs = 8_000): Promise<GeoPoint | null> {
  return new Promise((resolve) => {
    if (!navigator.geolocation) {
      resolve(null)
      return
    }
    const timer = window.setTimeout(() => resolve(null), timeoutMs)
    navigator.geolocation.getCurrentPosition(
      (position) => {
        window.clearTimeout(timer)
        resolve({ lng: position.coords.longitude, lat: position.coords.latitude })
      },
      () => {
        window.clearTimeout(timer)
        resolve(null)
      },
      { timeout: timeoutMs, maximumAge: 60_000 },
    )
  })
}