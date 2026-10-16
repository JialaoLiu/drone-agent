// 本地平面坐标（米，x 东、y 北，原点为场地经纬度）与网页墨卡托瓦片之间的换算。
// 卫星瓦片（Esri World Imagery）与场地内部同为 WGS-84，不需要偏移换算。
const R = 6378137

export interface Origin { lat: number; lon: number }

export function toLatLon(o: Origin, x: number, y: number): [number, number] {
  const lat = o.lat + (y / R) * (180 / Math.PI)
  const lon = o.lon + (x / (R * Math.cos((o.lat * Math.PI) / 180))) * (180 / Math.PI)
  return [lat, lon]
}

// 经纬度 → 某缩放级别下的全局像素坐标（每块瓦片 256 像素）
export function toPixel(lat: number, lon: number, z: number): [number, number] {
  const n = 256 * 2 ** z
  const x = ((lon + 180) / 360) * n
  const s = Math.sin((lat * Math.PI) / 180)
  const y = (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * n
  return [x, y]
}

export interface Tile { url: string; x: number; y: number; size: number }

// 覆盖本地范围 [x0,x1]×[y0,y1] 的瓦片，返回每块瓦片在本地坐标中的左上角（x 东、y 北）与边长（米）
export function tilesFor(o: Origin, x0: number, x1: number, y0: number, y1: number, z = 18): Tile[] {
  const [latTop, lonLeft] = toLatLon(o, x0, y1)
  const [latBottom, lonRight] = toLatLon(o, x1, y0)
  const [px0, py0] = toPixel(latTop, lonLeft, z)
  const [px1, py1] = toPixel(latBottom, lonRight, z)
  const [ox, oy] = toPixel(o.lat, o.lon, z)
  const mPerPx = (2 * Math.PI * R * Math.cos((o.lat * Math.PI) / 180)) / (256 * 2 ** z)
  const out: Tile[] = []
  for (let tx = Math.floor(px0 / 256); tx <= Math.floor(px1 / 256); tx++) {
    for (let ty = Math.floor(py0 / 256); ty <= Math.floor(py1 / 256); ty++) {
      out.push({
        url: `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${z}/${ty}/${tx}`,
        x: (tx * 256 - ox) * mPerPx, y: -(ty * 256 - oy) * mPerPx, size: 256 * mPerPx,
      })
    }
  }
  return out
}
export const IMAGERY_ATTRIBUTION = '卫星影像：Esri、Maxar、Earthstar Geographics'
