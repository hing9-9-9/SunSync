import { test } from "node:test";
import assert from "node:assert/strict";
import { blankSpace } from "./model.ts";
import { ADVANCE_MINUTES, applyShadingTime, blockingSchedule, firstProtectionContact, polygonsTouch, shadedCells, sunlightPolygon } from "./sunlight.ts";
function scene() {
  const s = blankSpace();
  s.regions = [{id:"r", name:"보호영역", zone:s.zone, furnitureId:null, x:6.3,y:3,width:0.1,depth:0.1,bottom:0,top:1,face:"상단",grade:"A"}];
  return s;
}
test("first contact uses the same geometry as the drawing and includes touch", () => {
  const s=scene(), time=firstProtectionContact(s);
  assert.notEqual(time,null);
  const r=s.regions[0], box=[{x:r.x,y:r.y},{x:r.x+r.width,y:r.y},{x:r.x+r.width,y:r.y+r.depth},{x:r.x,y:r.y+r.depth}];
  assert.equal(polygonsTouch(sunlightPolygon(s,time!/60),box),true);
  if(time!>0) assert.equal(polygonsTouch(sunlightPolygon(s,(time!-1)/60),box),false);
});
test("viewing another time cannot change the scheduled first contact", () => {
  const s=scene(), first=firstProtectionContact(s);
  for(const hour of [9,12,15,18]) { sunlightPolygon(s,hour); assert.equal(firstProtectionContact(s),first); }
});
test("no intersection, D grade, other zones and no protection produce no schedule", () => {
  const s=scene();s.regions[0].y=0;assert.equal(firstProtectionContact(s),null);
  s.regions[0].y=3;s.regions[0].grade="D";assert.equal(firstProtectionContact(s),null);
  s.regions[0].grade="A";s.regions[0].zone="다른 구역";assert.equal(firstProtectionContact(s),null);
  s.regions=[];assert.equal(firstProtectionContact(s),null);
});

test("full day includes night without sunlight and standalone protection areas", () => {
  const s=scene();
  for(const hour of [0,3,5.99,18,21,23.99]) assert.deepEqual(sunlightPolygon(s,hour), []);
  assert.ok(sunlightPolygon(s,6).length > 0);
  s.furniture=[];
  assert.notEqual(firstProtectionContact(s),null);
  assert.equal(polygonsTouch([], [{x:0,y:0},{x:1,y:0},{x:0,y:1}]),false);
});

test("blocking starts before the predicted arrival and ends when the light leaves", () => {
  const s=scene(), [period]=blockingSchedule(s).get("r")!;
  assert.equal(period.arrival, firstProtectionContact(s));
  assert.equal(period.start, period.arrival-ADVANCE_MINUTES);
  assert.ok(period.end>period.arrival);
  assert.equal(polygonsTouch(sunlightPolygon(s,period.end/60),[{x:6.3,y:3},{x:6.4,y:3},{x:6.4,y:3.1},{x:6.3,y:3.1}]),false);
});
test("grade D regions never request blocking", () => {
  const s=scene(); s.regions[0].grade="D";
  assert.equal(blockingSchedule(s).has("r"),false);
});
test("film cells are shaded only while a region is being protected", () => {
  const s=scene(); s.windowDimensions={width:8,height:1};
  const schedule=blockingSchedule(s), [period]=schedule.get("r")!;
  assert.equal(shadedCells(s,schedule,(period.start-1)/60).size,0);
  const cells=shadedCells(s,schedule,period.arrival/60);
  assert.ok(cells.size>0);
  assert.ok([...cells].every(i=>i>=0 && i<80*10));
});
test("no window means no film cells", () => {
  const s=scene();
  assert.equal(shadedCells(s,blockingSchedule(s),12).size,0);
});
test("higher sun shades a higher band of film rows", () => {
  const s=scene(); s.windowDimensions={width:8,height:1};
  const schedule=new Map([["r",[{arrival:0,start:0,end:24*60}]]]);
  const topRow=(hour:number)=>Math.min(...[...shadedCells(s,schedule,hour)].map(i=>Math.floor(i/80)));
  assert.equal(topRow(12),0);
  assert.ok(topRow(7)>topRow(10));
});
test("periods closer than the advance time merge instead of overlapping", () => {
  const s=scene();
  for(const periods of blockingSchedule(s).values())
    periods.forEach((p,i)=>{ if(i) assert.ok(p.start>periods[i-1].end); });
});

test("blocking only happens inside the user's chosen time", () => {
  const s=scene(); s.windowDimensions={width:8,height:1};
  const sun=blockingSchedule(s), [lit]=sun.get("r")!;
  const minute=lit.arrival+10, hhmm=(m:number)=>`${String(Math.floor(m/60)).padStart(2,"0")}:${String(m%60).padStart(2,"0")}`;
  // Off (or never set): nothing is blocked even while the sun reaches the region.
  assert.equal(shadedCells(s,applyShadingTime(sun,undefined),minute/60).size,0);
  assert.equal(shadedCells(s,applyShadingTime(sun,{enabled:false,start:"00:00",end:"23:50"}),minute/60).size,0);
  // On, and the chosen time covers the sunlit minute: cells are shaded.
  const inside=applyShadingTime(sun,{enabled:true,start:hhmm(minute-5),end:hhmm(minute+5)});
  assert.ok(shadedCells(s,inside,minute/60).size>0);
  const [clipped]=inside.get("r")!;
  assert.equal(clipped.start,minute-5); assert.equal(clipped.end,minute+5);
  // On, but the chosen time is outside the sunlit period: nothing to block.
  const outside=applyShadingTime(sun,{enabled:true,start:"00:10",end:"00:20"});
  assert.deepEqual(outside.get("r"),[]);
});
