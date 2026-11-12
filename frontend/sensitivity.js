/* Convex outline of equal-radius circles in the map's projected pixels. */
(function (root) {
  "use strict";
  function longitudeNear(longitude, reference) {
    return reference + ((((longitude - reference + 180) % 360) + 360) % 360 - 180);
  }
  function roundedEnvelope(centers, radius) {
    if (centers.length < 2) return [];
    const vertices = 32, padded = radius / Math.cos(Math.PI / vertices);
    const ring = Array.from({length: vertices}, (_, i) => {
      const t = (i + .5) * 2 * Math.PI / vertices;
      return [padded * Math.cos(t), padded * Math.sin(t)];
    });
    const sorted = centers.flatMap(([x,y]) => ring.map(([dx,dy]) => [x+dx,y+dy]))
      .sort((a,b) => a[0]-b[0] || a[1]-b[1]);
    const cross = (a,b,c) => (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0]);
    function half(points) {
      const out = [];
      for (const p of points) {
        while (out.length > 1 && cross(out.at(-2),out.at(-1),p) <= 0) out.pop();
        out.push(p);
      }
      return out.slice(0,-1);
    }
    return half(sorted).concat(half(sorted.reverse()));
  }
  const api = {longitudeNear,roundedEnvelope};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.BPSensitivity = api;
})(globalThis);
