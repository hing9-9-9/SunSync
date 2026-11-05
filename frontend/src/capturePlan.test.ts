import test from 'node:test';
import assert from 'node:assert/strict';
import { shapes, nextPhotoSlot, validPolygon } from './capturePlan.ts';
test('documented plans use 4/6/3/6/4 points, three photos at every point',()=>{
 for(const [shape,n] of [['circle',4],['ellipse',6],['triangle',3],['l-shape',6],['rectangle',4]] as const){
  assert.equal(shapes[shape].points.length,n);
  const accepted=Array.from({length:n},()=>[false,false,false]);
  for(let i=0;i<n*3;i++){const slot=nextPhotoSlot(accepted)!;assert.deepEqual(slot,{point:Math.floor(i/3),photo:i%3});accepted[slot.point][slot.photo]=true;}
  assert.equal(nextPhotoSlot(accepted),null);
  accepted[0][1]=false;assert.deepEqual(nextPhotoSlot(accepted),{point:0,photo:1});
 }
});
test('custom shape needs a non-crossing closed outline with distinct points',()=>{
 assert.equal(validPolygon(shapes.rectangle.points),true);
 assert.equal(validPolygon(shapes['l-shape'].points),true);
 assert.equal(validPolygon([{x:10,y:10},{x:20,y:20}]),false);
 assert.equal(validPolygon([{x:10,y:10},{x:20,y:20},{x:30,y:30}]),false);
 assert.equal(validPolygon([{x:10,y:10},{x:90,y:90},{x:10,y:90},{x:90,y:10}]),false);
});
