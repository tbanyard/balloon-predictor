"""Schedule different balloons to reach one AMSL altitude at one UTC time."""
from datetime import datetime, timedelta

from .errors import PredictorError
from .predictor import predict, run_flight


def plan_arrivals(request, store, progress=lambda message: None):
    entries = []
    for site, template in enumerate(request.flights):
        for rate in request.ascent_rates or [template.ascent_rate]:
            duration = (request.target_altitude - template.launch_altitude) / rate
            launch = request.arrival_datetime - timedelta(seconds=duration)
            name = f"{template.flight_name} · {rate:g} m/s"
            entry = {"index": len(entries), "site_index": site, "flight_name": name,
                     "ascent_rate": rate, "launch_datetime": launch.isoformat().replace("+00:00", "Z")}
            progress(f"Coordinating balloon {len(entries)+1} · {name}")
            try:
                if duration > template.max_duration_hours * 3600:
                    raise PredictorError("Estimated ascent exceeds maximum flight duration. Increase the maximum flight hours.")
                for iteration in range(12):
                    body = template.model_copy(update={"launch_datetime": launch, "flight_mode": "ascent",
                        "burst_altitude": request.target_altitude, "ascent_rate": rate,
                        "ensemble_members": 0, "flight_name": name})
                    weather = store.get(body.dataset, launch)
                    flight = run_flight(body, weather)
                    actual = datetime.fromisoformat(flight["summary"]["burst"]["datetime"].replace("Z", "+00:00"))
                    error = (actual - request.arrival_datetime).total_seconds()
                    if abs(error) <= .25:
                        break
                    # Includes the effect of atmospheric vertical wind. Each trial
                    # re-samples the atmosphere at the revised launch and track times.
                    launch -= timedelta(seconds=error)
                else:
                    raise PredictorError("Launch-time solver did not converge within 0.25 s. Revise vertical-wind settings or flight parameters.")
                result = predict(body, weather)
                entry.update(status="completed", result=result,
                             launch_datetime=body.launch_datetime.isoformat().replace("+00:00", "Z"),
                             arrival_error_seconds=error)
            except PredictorError as exc:
                entry.update(status="failed", error=str(exc))
            entries.append(entry)
    completed = [e for e in entries if e["status"] == "completed"]
    return {"type": "series", "schedule_type": "coordinated", "request": request.model_dump(mode="json"),
            "flights": entries, "completed": len(completed), "failed": len(entries)-len(completed),
            "total": len(entries), "target_altitude": request.target_altitude,
            "arrival_datetime": request.arrival_datetime.isoformat().replace("+00:00", "Z"),
            "arrival_tolerance_seconds": .25,
            "arrival_positions": [{"index": e["index"], "flight_name": e["flight_name"],
                                   **e["result"]["summary"]["burst"]} for e in completed],
            "window": "Launches are solved for a common target altitude and arrival UTC time; they stop at that altitude.",
            "notes": ["Ascent rates are relative to the air when atmospheric vertical wind is enabled.",
                      "Equal altitude and arrival time do not guarantee a straight spatial line; launch locations also affect alignment.",
                      "Connecting endpoint markers shows the predicted formation, not an additional balloon trajectory.",
                      "Sensitivity members are disabled for coordinated plans."]}
