/** The map's data, without a map.
 *
 *  A list row carries ONE geo point — its closest site to this patient — not
 *  every site the trial runs at. So a pin is "the nearest place this trial is
 *  open", and two trials at the same hospital are one pin carrying both. That
 *  is worth saying out loud, because a map of clinical trials invites the
 *  reading "here is everywhere I could go", and this is not that.
 *
 *  Kept separate from the rendering so the grouping, the bounds and the
 *  degenerate cases can be tested without a maps library or a DOM.
 */
import type { TrialMatch } from "./types";

export interface MapMarker {
  /** The place, taken from the northernmost-then-westernmost site in the
   *  group. NOT a grouping key: two trials are one pin when they are within
   *  `SAME_PLACE_METRES` of each other, so equal coordinates are sufficient
   *  for sharing a pin and not necessary. */
  latitude: number;
  longitude: number;
  /** What to call this place — the site title from the trial that is closest
   *  to the patient, falling back to the coordinates. Here rather than left to
   *  the host, so every renderer labels a pin the same way and none of them
   *  drifts when this rule changes. */
  name: string;
  /** Every trial whose closest site is here, best match first. */
  trials: TrialMatch[];
}

export interface MapBounds {
  north: number;
  south: number;
  east: number;
  west: number;
}

/** How close two sites have to be to count as one place, in metres. About a
 *  city block: two departments of one hospital are the same place to someone
 *  deciding whether they can get there, and two hospitals across town are not.
 *
 *  A DISTANCE rather than a rounded key. Rounding puts a hard edge somewhere,
 *  and points either side of it — 51.50749 and 51.50751, two metres apart —
 *  land in different buckets and draw as separate pins. */
const SAME_PLACE_METRES = 150;

/** Decimal places kept in the coordinates a marker reports. Five is about a
 *  metre, which is finer than the grouping and fine for a pin. */
const PRECISION = 5;

const key = (lat: number, lng: number) =>
  `${lat.toFixed(PRECISION)},${lng.toFixed(PRECISION)}`;

/** Metres between two points, flat-earth style. Over a city block the error is
 *  centimetres, and nothing here needs better. */
function metresBetween(
  aLat: number,
  aLng: number,
  bLat: number,
  bLng: number,
): number {
  const METRES_PER_DEGREE = 111_320;
  const meanLat = ((aLat + bLat) / 2) * (Math.PI / 180);
  const dLat = (aLat - bLat) * METRES_PER_DEGREE;
  // Wrapped, or two sites either side of the antimeridian — 179.9995 and
  // -179.9995, a hundred metres apart — read as nearly 360 degrees and never
  // group. The same wrap the bounds already take.
  let dLngDegrees = aLng - bLng;
  if (dLngDegrees > 180) dLngDegrees -= 360;
  if (dLngDegrees < -180) dLngDegrees += 360;
  const dLng = dLngDegrees * METRES_PER_DEGREE * Math.cos(meanLat);
  return Math.hypot(dLat, dLng);
}

/** A point this map can place. Shared by `buildMarkers` and
 *  `unmappableCount`, because two copies of this rule drift and the panel then
 *  drops a trial while reporting that none is missing. */
function isPlaceable(point: TrialMatch["closestLocationGeoPoint"]): boolean {
  if (!point) return false;
  const { latitude, longitude } = point;
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return false;
  return latitude >= -90 && latitude <= 90 && longitude >= -180 && longitude <= 180;
}

/** A marker's stable identity, for a caller holding on to one across a
 *  re-render. Object identity does not survive the list changing underneath. */
export const markerKey = (marker: MapMarker): string =>
  key(marker.latitude, marker.longitude);

const scoreOf = (trial: TrialMatch): number =>
  trial.matchScore ?? trial.goodnessScore ?? 0;

/** Trials with a usable location, grouped into pins.
 *
 *  Rows without a geo point are dropped — silently here, but the caller is
 *  expected to say how many, because a map that quietly shows fewer trials
 *  than the list is a map that lies by omission.
 */
export function buildMarkers(trials: TrialMatch[]): MapMarker[] {
  // Clustered in a fixed geographic order, NOT in the order the rows arrived.
  // First-fit grouping is not transitive — three buildings of one campus 140m
  // apart can come out as two groups or one depending on which is seen first —
  // and the row order is the reader's sort. Without this, changing the sort
  // changed how many places the map said there were, and moved the pins,
  // while nothing about the geography had changed.
  const placeable = trials
    .filter((t) => isPlaceable(t.closestLocationGeoPoint))
    .sort((a, b) => {
      const pa = a.closestLocationGeoPoint!;
      const pb = b.closestLocationGeoPoint!;
      return pb.latitude - pa.latitude || pa.longitude - pb.longitude;
    });

  const markers: MapMarker[] = [];
  for (const trial of placeable) {
    const { latitude, longitude } = trial.closestLocationGeoPoint!;
    // A page is ten to fifty rows, so the quadratic scan costs nothing and
    // needs no spatial index.
    const near = markers.find(
      (m) => metresBetween(m.latitude, m.longitude, latitude, longitude) <= SAME_PLACE_METRES,
    );
    if (near) {
      near.trials.push(trial);
      continue;
    }
    markers.push({
      // The first member in that fixed order, so the same set of trials always
      // produces the same coordinates — which is what makes `markerKey` a
      // key the caller can hold on to across a re-sort.
      latitude: Number(latitude.toFixed(PRECISION)),
      longitude: Number(longitude.toFixed(PRECISION)),
      name: "",
      trials: [trial],
    });
  }

  for (const marker of markers) {
    // Named BEFORE the score sort. The name is the nearest site's, and sorting
    // by score first would hand it to the best-matching trial instead — a pin
    // labelled with the wrong hospital whenever the two are different trials.
    marker.name = placeNameOf(marker);
    marker.trials.sort((a, b) => scoreOf(b) - scoreOf(a));
  }
  // Busiest first. This is reading order for the panel, not a z-order — the
  // host draws, and a host that maps this array to DOM nodes stacks LATER
  // entries on top.
  markers.sort((a, b) => b.trials.length - a.trials.length);
  return markers;
}

/** What to call a place: the site title from the trial nearest the patient.
 *
 *  Nearest, not best-matching — a pin is a place, and the reader is asking
 *  where it is. `distance` is null when the patient has no geography, in which
 *  case any member will do and the first in geographic order is taken.
 *
 *  `||` rather than `??` on the title: the serializer copies it verbatim, and
 *  an empty one would leave a button whose only content is "2 trials".
 */
function placeNameOf(marker: MapMarker): string {
  let nearest = marker.trials[0];
  for (const trial of marker.trials) {
    if (trial.distance == null) continue;
    if (nearest?.distance == null || trial.distance < nearest.distance) nearest = trial;
  }
  return (
    nearest?.location?.[0] ||
    `${marker.latitude.toFixed(4)}, ${marker.longitude.toFixed(4)}`
  );
}

/** How many rows the map cannot show. The number the caller has to print.
 *
 *  Counts a point that is present but unusable as well as one that is absent —
 *  they are the same thing to the reader, and counting only the absent ones
 *  lets the panel drop a trial while reporting that nothing is missing.
 */
export function unmappableCount(trials: TrialMatch[]): number {
  return trials.filter((t) => !isPlaceable(t.closestLocationGeoPoint)).length;
}

/** The box that holds every pin, or null when there is nothing to hold.
 *
 *  `west` MAY BE GREATER THAN `east`. That is not a bug to normalise away: it
 *  is how a box that crosses the antimeridian is expressed, and a host that
 *  computes `east - west` for a width gets a negative number for any patient
 *  with trials on both sides of the Pacific. Google's `LatLngBounds` reads it
 *  correctly as-is.
 *
 *  Padded, because a single pin gives a zero-area box — which a maps library
 *  will happily honour by zooming to street level on one building.
 */
export function boundsOf(markers: MapMarker[], padding = 0.05): MapBounds | null {
  if (!markers.length) return null;
  const lats = markers.map((m) => m.latitude);
  const north = Math.min(90, Math.max(...lats) + padding);
  const south = Math.max(-90, Math.min(...lats) - padding);
  const { east, west } = longitudeSpan(
    markers.map((m) => m.longitude),
    padding,
  );
  return { north, south, east, west };
}

/** The narrower of the two ways round the world.
 *
 *  Pins at 179 and -179 are a few kilometres apart, and a plain min/max reads
 *  them as 358 degrees and zooms out to the whole globe. When the wrapped
 *  interval is smaller this returns it with `west` GREATER than `east`, which
 *  is how a maps library is told the box crosses the antimeridian.
 */
function longitudeSpan(longitudes: number[], padding: number): { east: number; west: number } {
  const sorted = [...longitudes].sort((a, b) => a - b);
  const min = sorted[0];
  const max = sorted[sorted.length - 1];
  let widestGap = 360 - (max - min);
  let gapStart = max;
  let gapEnd = min;
  for (let i = 1; i < sorted.length; i += 1) {
    const gap = sorted[i] - sorted[i - 1];
    if (gap > widestGap) {
      widestGap = gap;
      gapStart = sorted[i - 1];
      gapEnd = sorted[i];
    }
  }
  // The pins occupy everything OUTSIDE the widest gap between them.
  const west = gapEnd === min && gapStart === max ? min : gapEnd;
  const east = gapEnd === min && gapStart === max ? max : gapStart;
  const pad = (value: number) => ((((value + 180) % 360) + 360) % 360) - 180;
  return { west: pad(west - padding), east: pad(east + padding) };
}
