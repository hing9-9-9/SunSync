export type Shape = 'rectangle' | 'circle' | 'ellipse' | 'triangle' | 'l-shape' | 'polygon';
export type CapturePoint = { x: number; y: number };
export const photosPerPoint = 3;
export const shapes: Record<Shape, { label: string; points: CapturePoint[] }> = {
 rectangle: { label: '직사각형', points: [{x:20,y:25},{x:80,y:25},{x:80,y:75},{x:20,y:75}] },
 circle: { label: '원형', points: [{x:50,y:15},{x:85,y:50},{x:50,y:85},{x:15,y:50}] },
 ellipse: { label: '타원형', points: [{x:50,y:25},{x:78,y:32},{x:90,y:50},{x:50,y:75},{x:22,y:68},{x:10,y:50}] },
 triangle: { label: '삼각형', points: [{x:50,y:15},{x:85,y:80},{x:15,y:80}] },
 'l-shape': { label: 'L자형', points: [{x:20,y:20},{x:55,y:20},{x:55,y:52},{x:85,y:52},{x:85,y:85},{x:20,y:85}] },
 polygon: { label: '직접 그리기', points: [] },
};
export function validPolygon(points: CapturePoint[]) {
 if (points.length < 3) return false;
 const cross = (a:CapturePoint,b:CapturePoint,c:CapturePoint)=>(b.x-a.x)*(c.y-a.y)-(b.y-a.y)*(c.x-a.x);
 const area=points.reduce((n,p,i)=>{const q=points[(i+1)%points.length];return n+p.x*q.y-q.x*p.y;},0);
 if(Math.abs(area)<20) return false;
 for(let i=0;i<points.length;i++) for(let j=i+1;j<points.length;j++) {
  if(Math.hypot(points[i].x-points[j].x,points[i].y-points[j].y)<3) return false;
  if(j===i+1 || (i===0&&j===points.length-1)) continue;
  const a=points[i],b=points[(i+1)%points.length],c=points[j],d=points[(j+1)%points.length];
  if(Math.max(Math.min(a.x,b.x),Math.min(c.x,d.x))<=Math.min(Math.max(a.x,b.x),Math.max(c.x,d.x)) && Math.max(Math.min(a.y,b.y),Math.min(c.y,d.y))<=Math.min(Math.max(a.y,b.y),Math.max(c.y,d.y)) && cross(a,b,c)*cross(a,b,d)<=0 && cross(c,d,a)*cross(c,d,b)<=0) return false;
 }
 return true;
}
export function nextPhotoSlot(accepted: boolean[][]): { point: number; photo: number } | null {
 for(let point=0;point<accepted.length;point++) for(let photo=0;photo<photosPerPoint;photo++) if(!accepted[point]?.[photo]) return {point,photo};
 return null;
}
