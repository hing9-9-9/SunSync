import Plan from "./Plan";
import { FilmGrid } from "./ShadingPlan";
import { exampleState } from "./exampleSpace";
import { regionForFurniture } from "./model";
import { applyShadingTime, blockingSchedule } from "./sunlight";

// The three steps show the app's own plan and film grid, drawn from the sample space.
const sample = exampleState().spaces[0];
// One shelf stands in the afternoon light so the fragments show a protected area being shaded.
const lit = { ...sample.furniture[2], x: 4.4, y: 2.8 };
const demo = {
  ...sample,
  furniture: [sample.furniture[0], sample.furniture[1], lit],
  regions: [{ ...regionForFurniture(lit), grade: "B" as const }],
  windowDimensions: { width: 4, height: 1.8, wall: "south" as const },
};
const noop = () => {};
const demoSchedule = applyShadingTime(blockingSchedule(demo), { enabled: true, start: "06:00", end: "18:00" });
// Show the moment the sample shelf is shaded, so the film fragment is never blank.
const demoPeriod = [...demoSchedule.values()].flat()[0];
const demoHour = demoPeriod ? (demoPeriod.start + demoPeriod.end) / 120 : 15;
// Real app fragments for the home steps and the guide; drawn from the sample space above.
export const demoShots = {
  plan: <Plan space={{ ...demo, regions: [] }} selectedFurniture={null} selectedRegion={null} onFurniture={noop} onRegion={noop} showDemoSun={false} />,
  protect: <Plan space={demo} selectedFurniture={null} selectedRegion={demo.regions[0].id} onFurniture={noop} onRegion={noop} hour={demoHour} />,
  marker: <FilmGrid space={{ ...demo, windowDimensions: { ...demo.windowDimensions, marker: { size: 0.3, x: 2, y: 1.1 } } }} marker />,
  film: <FilmGrid space={demo} hour={demoHour} schedule={demoSchedule} />,
};
