// Approximate sun position (NOAA low-precision formulas, about 1° error) for the 3D sample.
// shortcut: ignores refraction and the equation-of-time drift between years; use FR-01 output when the 3D view goes live.
export type SunPosition = { altitude: number; azimuth: number };
const rad = Math.PI / 180;

/** Degrees; azimuth is clockwise from north. `hour` is local clock time in a zone `utcOffset` hours from UTC. */
export function sunPosition(dayOfYear: number, hour: number, latitude: number, longitude: number, utcOffset: number): SunPosition {
  const g = ((2 * Math.PI) / 365) * (dayOfYear - 1 + (hour - 12) / 24);
  const eot = 229.18 * (0.000075 + 0.001868 * Math.cos(g) - 0.032077 * Math.sin(g) - 0.014615 * Math.cos(2 * g) - 0.040849 * Math.sin(2 * g));
  const decl = 0.006918 - 0.399912 * Math.cos(g) + 0.070257 * Math.sin(g) - 0.006758 * Math.cos(2 * g) + 0.000907 * Math.sin(2 * g);
  const solarMinutes = hour * 60 + eot + 4 * longitude - 60 * utcOffset;
  const h = (solarMinutes / 4 - 180) * rad;
  const lat = latitude * rad;
  const sinAlt = Math.sin(lat) * Math.sin(decl) + Math.cos(lat) * Math.cos(decl) * Math.cos(h);
  const altitude = Math.asin(sinAlt);
  const azimuth = Math.atan2(Math.sin(h), Math.cos(h) * Math.sin(lat) - Math.tan(decl) * Math.cos(lat)) + Math.PI;
  return { altitude: altitude / rad, azimuth: azimuth / rad };
}

export function dayOfYear(month: number, day: number) {
  return Math.round((Date.UTC(2026, month - 1, day) - Date.UTC(2026, 0, 0)) / 86400000);
}
