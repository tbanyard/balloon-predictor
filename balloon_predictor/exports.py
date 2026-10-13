import csv
from io import StringIO
import json
from xml.etree.ElementTree import Element, SubElement, tostring, fromstring


def segments(points):
    """Split at ±180°, adding an interpolated crossing rather than a world-spanning line."""
    result = [[]]
    previous = None
    for point in points:
        coordinate = [point["longitude"], point["latitude"], point["altitude"]]
        if previous and abs(coordinate[0] - previous[0]) > 180:
            adjusted = coordinate[0] + (360 if coordinate[0] < previous[0] else -360)
            edge = 180 if previous[0] > 0 else -180
            fraction = (edge - previous[0]) / (adjusted - previous[0])
            crossing = [edge, previous[1] + fraction * (coordinate[1] - previous[1]),
                        previous[2] + fraction * (coordinate[2] - previous[2])]
            result[-1].append(crossing)
            result.append([[-edge, crossing[1], crossing[2]]])
        result[-1].append(coordinate)
        previous = coordinate
    return result


def geojson(result):
    if result.get("type")=="series":
        features=[]
        for entry in result["flights"]:
            if entry["status"]!="completed": continue
            for feature in geojson(entry["result"])["features"]:
                feature["properties"].update(flight_index=entry["index"],flight_name=entry["flight_name"],launch_datetime=entry["launch_datetime"])
                features.append(feature)
        return {"type":"FeatureCollection","features":features,"metadata":{"request":result["request"],
                "failed_launches":[e for e in result["flights"] if e["status"]=="failed"]}}
    features = []
    for stage in result["prediction"]:
        lines = segments(stage["trajectory"])
        geometry = {"type": "LineString", "coordinates": lines[0]} if len(lines) == 1 else {"type": "MultiLineString", "coordinates": lines}
        features.append({"type": "Feature", "geometry": geometry,
                         "properties": {"stage": stage["stage"], "dataset": result["weather"]["id"],
                                        "times": [p["datetime"] for p in stage["trajectory"]], "altitude_reference": "AMSL"}})
    for name in ("launch", "burst", "landing"):
        point = result["summary"][name]
        if point is None: continue
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [
            point["longitude"], point["latitude"], point["altitude"]]}, "properties": {"event": name, **point}})
    for member in result.get("ensemble", []):
        point = member["summary"].get("endpoint",member["summary"]["landing"])
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [
            point["longitude"], point["latitude"], point["altitude"]]},
                         "properties": {"event": "sensitivity_"+member["summary"].get("endpoint_kind","landing"), "member": member["member"]}})
    return {"type": "FeatureCollection", "features": features, "metadata": {
        "weather": result["weather"], "request": result["request"], "warnings": result["warnings"]}}


def csv_export(result):
    if result.get("type")=="series":
        buffer=StringIO(newline=""); writer=csv.writer(buffer)
        writer.writerow(["flight_index","flight_name","member","stage","datetime_utc","latitude_deg","longitude_deg","altitude_m_amsl","elapsed_seconds"])
        for entry in result["flights"]:
            if entry["status"]!="completed":continue
            rows=csv.reader(StringIO(csv_export(entry["result"])))
            next(rows)
            for row in rows:writer.writerow([entry["index"],entry["flight_name"],*row])
        return buffer.getvalue()
    buffer = StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(["member", "stage", "datetime_utc", "latitude_deg", "longitude_deg", "altitude_m_amsl", "elapsed_seconds"])
    flights = [(0, result)] + [(member["member"], member) for member in result.get("ensemble", [])]
    for member_id, flight in flights:
        for stage in flight["prediction"]:
            for point in stage["trajectory"]:
                writer.writerow([member_id, stage["stage"], point["datetime"], point["latitude"],
                                 point["longitude"], point["altitude"], point["elapsed_seconds"]])
    return buffer.getvalue()


def kml_export(result):
    if result.get("type")=="series":
        root=Element("kml",xmlns="http://www.opengis.net/kml/2.2"); doc=SubElement(root,"Document")
        for entry in result["flights"]:
            if entry["status"]!="completed":continue
            folder=SubElement(doc,"Folder"); SubElement(folder,"name").text=entry["flight_name"]+" · "+entry["launch_datetime"]
            child=fromstring(kml_export(entry["result"]))
            for mark in child.findall(".//{http://www.opengis.net/kml/2.2}Placemark"):folder.append(mark)
        return tostring(root,encoding="utf-8",xml_declaration=True).decode()
    root = Element("kml", xmlns="http://www.opengis.net/kml/2.2")
    doc = SubElement(root, "Document")
    SubElement(doc, "name").text = "Balloon prediction"
    SubElement(doc, "description").text = json.dumps({"weather": result["weather"], "warnings": result["warnings"]})
    for stage in result["prediction"]:
        mark = SubElement(doc, "Placemark")
        SubElement(mark, "name").text = stage["stage"]
        multi = SubElement(mark, "MultiGeometry")
        for line in segments(stage["trajectory"]):
            shape = SubElement(multi, "LineString")
            SubElement(shape, "altitudeMode").text = "absolute"
            SubElement(shape, "coordinates").text = " ".join(",".join(map(str, p)) for p in line)
    for name in ("launch", "burst", "landing"):
        p = result["summary"][name]
        if p is None: continue
        mark = SubElement(doc, "Placemark")
        SubElement(mark, "name").text = name
        point = SubElement(mark, "Point")
        SubElement(point, "altitudeMode").text = "absolute"
        SubElement(point, "coordinates").text = f"{p['longitude']},{p['latitude']},{p['altitude']}"
    return tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
