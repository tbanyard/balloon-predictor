class PredictorError(Exception):
    """An actionable error that is safe to display to a user."""


class CoverageError(PredictorError):
    """The flight has left the available weather coverage."""


class MissingTerrainError(CoverageError):
    """A specific required physical elevation sample was unavailable."""

    def __init__(self, latitude, longitude, tile, downloadable, phase=None, no_public_tile=False):
        self.latitude = float(latitude)
        self.longitude = float(longitude)
        self.tile = tile
        self.downloadable = downloadable
        self.phase = phase
        self.no_public_tile = no_public_tile
        context = {"launch": " at the launch point", "descent": " along the descent"}[phase] if phase else " here"
        point = f" ({latitude:.5f}°, {longitude:.5f}°)"
        advice = ("Download the required GLO-30 tile or add a covering GeoTIFF."
                  if downloadable else "Add a covering GeoTIFF or select a known ground elevation.")
        reason = "No public GLO-30 tile" if no_public_tile else "No valid physical DEM pixel"
        super().__init__(f"{reason}{context}{point}. {advice} Missing pixels are never treated as ocean.")

    def during(self, phase):
        return MissingTerrainError(self.latitude, self.longitude, self.tile, self.downloadable, phase, self.no_public_tile)

    def details(self):
        return {"code": "missing_dem", "latitude": self.latitude, "longitude": self.longitude,
                "tile": self.tile, "downloadable": self.downloadable, "phase": self.phase}
